import contextlib
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import override, Generator

from moncic.context import privs
from moncic.container import ContainerConfig
from moncic.utils import setns
from moncic.utils.deb import apt_get_cmd
from moncic.utils.fs import cd
from moncic.utils.guest import guest_only, host_only
from moncic.utils.run import run
from moncic.utils.script import Script
from .build import Build


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
class Debian(Build):
    """
    Build Debian packages
    """

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

    @guest_only
    def get_build_deps_in_container(self) -> list[str]:
        res = subprocess.run(
            ["/srv/moncic-ci/dpkg-listbuilddeps"],
            stdout=subprocess.PIPE,
            text=True,
            check=True,
            cwd=self.source.path.as_posix(),
        )
        return [name.strip() for name in res.stdout.strip().splitlines()]

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

    @override
    @guest_only
    def build(self) -> None:
        from ..source.debian import DebianSource

        assert isinstance(self.source, DebianSource)
        # Build source package
        with privs.user():
            dsc_path = self.source.build_source_package()

        self.name = self.source.source_info.name

        if not self.source_only:
            self.build_binary(dsc_path)

        self.success = True

    @guest_only
    def build_binary(self, dsc_path: Path) -> None:
        """
        Build binary packages
        """
        if self.build_profile:
            profiles: list[str] = []
            options: list[str] = []
            for entry in self.build_profile.split():
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
                if self.include_source:
                    cmd.append("-sa")
                self.trace_run(cmd, env=env)

    @override
    @host_only
    def collect_artifacts(self, script: Script) -> None:
        dest = Path("/srv/moncic-ci/artifacts")
        for path in "/srv/moncic-ci/source", "/srv/moncic-ci/build":
            with script.for_("f", f"$(find {shlex.quote(path)} -maxdepth 1 -type f)"):
                script.run_unquoted(f'mv "$f" {shlex.quote(dest.as_posix())}')
