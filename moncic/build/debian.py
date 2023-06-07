from __future__ import annotations

# import importlib.resources
import logging
import os
# import shutil
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, Optional

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


class SourceInfo(NamedTuple):
    srcname: str
    version: str
    dsc_fname: str
    tar_fname: str


@guest_only
def get_source_info(path=".") -> SourceInfo:
    """
    Return the file name of the .dsc file that would be created by the debian
    source package in the current directory
    """
    with cd(path):
        # Taken from debspawn
        pkg_srcname = None
        pkg_version = None
        res = run(["dpkg-parsechangelog"], stdout=subprocess.PIPE, text=True)
        for line in res.stdout.splitlines():
            if line.startswith('Source: '):
                pkg_srcname = line[8:].strip()
            elif line.startswith('Version: '):
                pkg_version = line[9:].strip()

        if not pkg_srcname or not pkg_version:
            raise RuntimeError("Unable to determine source package name or source package version")

        pkg_version_dsc = pkg_version.split(":", 1)[1] if ":" in pkg_version else pkg_version
        dsc_fname = f"{pkg_srcname}_{pkg_version_dsc}.dsc"
        pkg_version_tar = pkg_version_dsc.split("-", 1)[0] if "-" in pkg_version_dsc else pkg_version_dsc
        tar_fname = f"{pkg_srcname}_{pkg_version_tar}.orig.tar.gz"

    return SourceInfo(pkg_srcname, pkg_version, dsc_fname, tar_fname)


def get_file_list(path: str) -> list[str]:
    """
    Read a .dsc or .changes file and return the list of files it references
    """
    res: list[str] = []
    is_changes = path.endswith(".changes")
    with open(path, "rt") as fd:
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
                    """})

    def __post_init__(self) -> None:
        # This is only set in guest systems, and after self.build_source() has
        # been called
        self.srcinfo: Optional[SourceInfo] = None

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
                stdout=subprocess.PIPE, text=True, check=True, cwd=self.source.guest_path)
        return [name.strip() for name in res.stdout.strip().splitlines()]

    @guest_only
    def setup_container_guest(self, system: System):
        super().setup_container_guest(system)

        # TODO: run apt update if the apt index is older than some threshold

        # Disable reindexing of manpages during installation of build-dependencies
        run(["debconf-set-selections"], input="man-db man-db/auto-update boolean false\n", text=True)

        if self.build_profile:
            os.environ["DEB_BUILD_PROFILES"] = self.build_profile

        # Log DEB_* env variables
        for k, v in os.environ.items():
            if k.startswith("DEB_"):
                log.debug("%s: %r", k, v)

    @guest_only
    def build(self) -> None:
        # Build source package
        with context.moncic.get().privs.user():
            self.srcinfo = get_source_info(self.source.guest_path)
            dsc_fname = self.source.build_source_package()

        if self.srcinfo is None:
            raise RuntimeError("source information has not been detected")

        self.name = self.srcinfo.srcname

        if not self.source_only:
            self.build_binary(dsc_fname)

        for path in "/srv/moncic-ci/source", "/srv/moncic-ci/build":
            with os.scandir(path) as it:
                for de in it:
                    if de.is_file():
                        self.artifacts.append(de.path)

        self.success = True

    @guest_only
    def build_binary(self, dsc_fname: str):
        """
        Build binary packages
        """
        if self.srcinfo is None:
            raise RuntimeError("source information not collected at build_binary time")
        with cd("/srv/moncic-ci/build"):
            self.trace_run(["dpkg-source", "-x", dsc_fname])

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
                # Use unshare to disable networking
                self.trace_run(["dpkg-buildpackage", "--no-sign"])

    @host_only
    def collect_artifacts(self, container: Container, destdir: str):
        container_root = container.get_root()
        user = UserConfig.from_sudoer()
        for path in self.artifacts:
            log.info("Copying %s to %s", path, destdir)
            link_or_copy(os.path.join(container_root, path.lstrip("/")), destdir, user=user)
        self.artifacts = [os.path.basename(path) for path in self.artifacts]
