from __future__ import annotations

# import importlib.resources
import logging
import os

# import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .. import context
from ..runner import UserConfig
from ..utils import setns
from ..utils.deb import apt_get_cmd
from ..utils.fs import cd
from ..utils.guest import guest_only, host_only
from ..utils.run import run
from .build import Build
from .utils import link_or_copy

if TYPE_CHECKING:
    from ..container import Container
    from ..system import System

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

    # @host_only
    # def get_build_deps(self) -> list[str]:
    #     with self.container() as container:
    #         # Inject a perl script that uses libdpkg-perl to compute the dependency list
    #         with importlib.resources.open_binary("moncic.build", "debian-dpkg-listbuilddeps") as fdin:
    #             with open(
    #                     os.path.join(container.get_root(), "srv", "moncic-ci", "dpkg-listbuilddeps"), "wb") as fdout:
    #                 shutil.copyfileobj(fdin, fdout)
    #                 os.fchmod(fdout.fileno(), 0o755)

    #         # Build run config
    #         run_config = container.config.run_config()

    #         return container.run_callable(
    #                 self.get_build_deps_in_container,
    #                 run_config).result()

    @guest_only
    def get_build_deps_in_container(self):
        res = subprocess.run(
            ["/srv/moncic-ci/dpkg-listbuilddeps"],
            stdout=subprocess.PIPE,
            text=True,
            check=True,
            cwd=self.guest_source_path.as_posix(),
        )
        return [name.strip() for name in res.stdout.strip().splitlines()]

    @guest_only
    def setup_container_guest(self, system: System):
        super().setup_container_guest(system)

        # TODO: run apt update if the apt index is older than some threshold

        # Disable reindexing of manpages during installation of build-dependencies
        run(["debconf-set-selections"], input="man-db man-db/auto-update boolean false\n", text=True)

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

    @guest_only
    def build(self) -> None:
        from ..source.debian import DebianSource

        assert isinstance(self.guest_source, DebianSource)
        # Build source package
        with context.moncic.get().privs.user():
            dsc_path = self.guest_source.build_source_package()

        self.name = self.guest_source.source_info.name

        if not self.source_only:
            self.build_binary(dsc_path)

        for path in "/srv/moncic-ci/source", "/srv/moncic-ci/build":
            with os.scandir(path) as it:
                for de in it:
                    if de.is_file():
                        self.artifacts.append(de.path)

        self.success = True

    @guest_only
    def build_binary(self, dsc_path: Path):
        """
        Build binary packages
        """
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

    @host_only
    def collect_artifacts(self, container: Container, destdir: str):
        container_root = container.get_root()
        user = UserConfig.from_sudoer()
        for path in self.artifacts:
            log.info("Copying %s to %s", path, destdir)
            link_or_copy(os.path.join(container_root, path.lstrip("/")), destdir, user=user)
        self.artifacts = [os.path.basename(path) for path in self.artifacts]
