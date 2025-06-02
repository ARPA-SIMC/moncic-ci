import abc
import contextlib
import logging
import dataclasses
import os
import os.path
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import override
from collections.abc import Generator

from moncic.runner import UserConfig
from moncic.container import ContainerConfig, Container
from moncic.source.debian import DebianGBP, DebianSource
from moncic.utils import setns
from moncic.utils.deb import apt_get_cmd
from moncic.utils.fs import cd
from moncic.utils.guest import guest_only, host_only
from moncic.utils.run import run
from moncic.utils.script import Script
from .build import Builder, BuildConfig, BuildResults


log = logging.getLogger(__name__)


def get_file_list(path: str) -> list[str]:
    """
    Read a .dsc or .changes file and return the list of files it references
    """
    res: list[str] = []
    is_changes = path.endswith(".changes")
    with open(path) as fd:
        in_files_section = False
        for line in fd:
            if in_files_section:
                if not line[0].isspace():
                    in_files_section = False
                else:
                    if is_changes:
                        checksum, size, section, priority, fname = line.strip().split(None, 4)
                    else:
                        checksum, size, fname = line.strip().split(None, 2)
                    res.append(fname)
            else:
                if line.startswith("Files:"):
                    in_files_section = True
    return res


@dataclass
class DebianBuildConfig(BuildConfig):
    build_profile: str = field(
        default="",
        metadata={
            "doc": """
                    space-separate list of Debian build profile to pass as DEB_BUILD_PROFILE
                    """
        },
    )
    include_source: bool = field(
        default=False,
        metadata={"doc": "Always include sources in upload (run `dpkg-buildpackage -sa`)"},
    )


class DebianBuilder(Builder):
    """
    Build Debian packages
    """

    build_config_class = DebianBuildConfig

    config: DebianBuildConfig

    source: DebianSource

    # @guest_only
    # def get_build_deps_in_container(self) -> list[str]:
    #     res = subprocess.run(
    #         ["/srv/moncic-ci/dpkg-listbuilddeps"],
    #         stdout=subprocess.PIPE,
    #         text=True,
    #         check=True,
    #         cwd=self.source.path.as_posix(),
    #     )
    #     return [name.strip() for name in res.stdout.strip().splitlines()]

    @override
    @contextlib.contextmanager
    def operation_plugin(self, config: ContainerConfig) -> Generator[None, None, None]:
        script = Script("Prepare Debian system for build", cwd=Path("/"), root=True)
        script.run_unquoted(
            "echo man-db man-db/auto-update boolean false | debconf-set-selections",
            description="Disable reindexing of manpages during installation of build-dependencies",
        )
        with super().operation_plugin(config):
            config.add_guest_scripts(setup=script)
            yield

    @abc.abstractmethod
    def build_source(self, container: Container) -> Path:
        """
        Build the source package.

        :returns: the guest path of the .dsc file
        """

    @override
    def build(self, container: Container) -> None:
        self.results.name = self.source.source_info.name
        guest_dsc_path = self.build_source(container)
        log.info("Source built as %s", guest_dsc_path)

        # TODO: Legacy: build operations performed inside the guest system

        # Build run config
        run_config = container.config.run_config()
        run_config.user = UserConfig.root()
        # Log run config
        for fld in dataclasses.fields(run_config):
            log.debug("run:%s = %r", fld.name, getattr(run_config, fld.name))

        self.results = container.run_callable(self.guest_main, run_config, args=(guest_dsc_path,))

    @guest_only
    def guest_main(self, guest_dsc_path: Path) -> BuildResults:
        """Run the build inside the guest system."""
        from ..source.debian import DebianSource

        assert isinstance(self.source, DebianSource)

        self.source = self.source.in_path(self.guest_source_path)

        if not self.config.source_only:
            self.build_binary(guest_dsc_path)

        self.results.success = True
        return self.results

    @guest_only
    def build_binary(self, dsc_path: Path) -> None:
        """
        Build binary packages
        """
        if self.config.build_profile:
            profiles: list[str] = []
            options: list[str] = []
            for entry in self.config.build_profile.split():
                if entry in ("nocheck", "nodoc"):
                    profiles.append(entry)
                    options.append(entry)
                elif entry.startswith(
                    (
                        "parallel=",
                        "nostrip",
                        "terse",
                        "hardening=",
                        "reproducibile=",
                        "abi=",
                        "future=",
                        "qa=",
                        "optimize=",
                        "sanitize=",
                    )
                ):
                    options.append(entry)
                else:
                    profiles.append(entry)

            os.environ["DEB_BUILD_PROFILES"] = " ".join(profiles)
            os.environ["DEB_BUILD_OPTIONS"] = " ".join(options)

        # Log DEB_* env variables
        for k, v in os.environ.items():
            if k.startswith("DEB_"):
                log.debug("%s=%r", k, v)

        with cd("/srv/moncic-ci/build"):
            self.trace_run(["dpkg-source", "-x", dsc_path.as_posix()])

            # Find the newly created build directory
            with os.scandir(".") as it:
                for de in it:
                    if de.is_dir():
                        builddir = de.path
                        break
                else:
                    raise RuntimeError("build directory not found")

            with cd(builddir):
                # Install build dependencies
                env = dict(os.environ)
                env.update(DEBIAN_FRONTEND="noninteractive")
                self.trace_run(apt_get_cmd("build-dep", "./"), env=env)

                # Build dependencies are installed, we don't need internet
                # anymore: Debian packages are required to build without
                # network access
                setns.unshare(setns.CLONE_NEWNET)

                # But we do need a working loopback
                run(["ip", "link", "set", "dev", "lo", "up"])

                # Build
                cmd = ["dpkg-buildpackage", "--no-sign"]
                if self.config.include_source:
                    cmd.append("-sa")
                self.trace_run(cmd, env=env)

    @override
    @host_only
    def collect_artifacts_script(self) -> Script:
        script = super().collect_artifacts_script()
        dest = Path("/srv/moncic-ci/artifacts")
        for path in "/srv/moncic-ci/source", "/srv/moncic-ci/build":
            with script.for_("f", f"$(find {shlex.quote(path)} -maxdepth 1 -type f)"):
                script.run_unquoted(f'mv "$f" {shlex.quote(dest.as_posix())}')
        return script


class DebianBuilderDsc(DebianBuilder):
    """Build a package from a DebianDsc source."""

    @override
    def build_source(self, container: Container) -> Path:
        return self.guest_source_path


class DebianBuildSource(DebianBuilder):
    def _find_built_dsc(self, container: Container) -> Path:
        res = container.run(
            [
                "/usr/bin/find",
                self.guest_source_path.parent.as_posix(),
                "-maxdepth",
                "1",
                "-type",
                "f",
                "-name",
                "*.dsc",
            ],
        )
        files = [os.path.basename(f) for f in res.stdout.decode().splitlines()]
        if self.source.source_info.dsc_filename in files:
            return self.guest_source_path.parent / self.source.source_info.dsc_filename

        # Something unexpected happened: look harder for a built .dsc file
        match len(files):
            case 0:
                raise RuntimeError("No source .dsc files found after building the source package")
            case 1:
                log.warning("found .dsc file %s instead of %s", files[0], self.source.source_info.dsc_filename)
                return self.guest_source_path.parent / files[0]
            case _:
                log.warning(
                    "found .dsc files %s instead of %s: picking %s",
                    files,
                    self.source.source_info.dsc_filename,
                    files[0],
                )
                return self.guest_source_path.parent / files[0]


class DebianBuilderDir(DebianBuildSource):
    """Build a package from a DebianDir source."""

    @override
    def build_source(self, container: Container) -> Path:
        # Uses --no-pre-clean to avoid requiring build-deps to be installed at
        # this stage
        script = Script("Build source package")
        script.run(["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"], cwd=self.guest_source_path)
        container.run_script(script)
        self.results.scripts.append(script)
        return self._find_built_dsc(container)


class DebianBuilderGBP(DebianBuildSource):
    """Build a package from a DebianGBP source."""

    source: DebianGBP

    @override
    def build_source(self, container: Container) -> Path:
        script = Script("Build source package")
        script.run(
            ["gbp", "buildpackage", "--git-ignore-new", "-d", "-S", "--no-sign", "--no-pre-clean"]
            + self.source.gbp_args,
            cwd=self.guest_source_path,
        )
        container.run_script(script)
        self.results.scripts.append(script)
        return self._find_built_dsc(container)
