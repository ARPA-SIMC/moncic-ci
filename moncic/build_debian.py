from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import TYPE_CHECKING, List, NamedTuple, Optional

from .deb import apt_get_cmd
from .runner import UserConfig
from .utils import cd
from . import setns
from .build import Builder, run, link_or_copy

if TYPE_CHECKING:
    from .container import Container

log = logging.getLogger(__name__)


class SourceInfo(NamedTuple):
    srcname: str
    version: str
    dsc_fname: str
    tar_fname: str
    changes_fname: str


def get_source_info() -> SourceInfo:
    """
    Return the file name of the .dsc file that would be created by the debian
    source package in the current directory
    """
    # Taken from debspawn
    pkg_srcname = None
    pkg_version = None
    res = run(["dpkg-parsechangelog"], capture_output=True, text=True)
    for line in res.stdout.splitlines():
        if line.startswith('Source: '):
            pkg_srcname = line[8:].strip()
        elif line.startswith('Version: '):
            pkg_version = line[9:].strip()

    if not pkg_srcname or not pkg_version:
        raise RuntimeError("Unable to determine source package name or source package version")

    res = run(["dpkg", "--print-architecture"], capture_output=True, text=True)
    arch = res.stdout.strip()

    pkg_version_dsc = pkg_version.split(":", 1)[1] if ":" in pkg_version else pkg_version
    dsc_fname = f"{pkg_srcname}_{pkg_version_dsc}.dsc"
    changes_fname = f"{pkg_srcname}_{pkg_version_dsc}_{arch}.changes"
    pkg_version_tar = pkg_version_dsc.split("-", 1)[0] if "-" in pkg_version_dsc else pkg_version_dsc
    tar_fname = f"{pkg_srcname}_{pkg_version_tar}.orig.tar.gz"

    return SourceInfo(pkg_srcname, pkg_version, dsc_fname, tar_fname, changes_fname)


def get_file_list(path: str) -> List[str]:
    """
    Read a .dsc or .changes file and return the list of files it references
    """
    res: List[str] = []
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


@Builder.register
class Debian(Builder):
    @classmethod
    def builds(cls, srcdir: str) -> bool:
        if os.path.isdir(os.path.join(srcdir, "debian")):
            return True
        return False

    def build_source_plain(self, srcinfo: SourceInfo, workdir: str):
        with self.system.images.session.moncic.privs.user():
            with tempfile.TemporaryDirectory(dir=workdir) as clean_src:
                # Make a clean clone to avoid building from a dirty working
                # directory
                run(["git", "clone", ".", clean_src])

                with cd(clean_src):
                    # Build upstream tarball
                    # FIXME: this is a hack, that prevents building new debian versions
                    #        from the same upstream tarball
                    run(["git", "archive", f"--output=../{srcinfo.tar_fname}", "HEAD"])

                    # Uses --no-pre-clean to avoid requiring build-deps to be installed at
                    # this stage
                    run(["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"])

                    # No need to copy .dsc and its assets to the work
                    # directory, since we're building on a temporary subdir inside it

    def build_source_gbp(self, srcinfo: SourceInfo, workdir: str):
        with self.system.images.session.moncic.privs.user():
            with open(os.path.expanduser("~/.gbp.conf"), "wt") as fd:
                fd.write(f"[DEFAULT]\nexport-dir={workdir}\n")
                fd.flush()
            run(["gbp", "buildpackage", "--git-ignore-new",
                 "--git-upstream-tree=branch", "-d", "-S", "--no-sign",
                 "--no-pre-clean"])

    def build_in_container(self, workdir: str) -> Optional[int]:
        # TODO:
        # - inject dependency packages in a private apt repo if required
        #    - or export a local apt repo readonly

        os.chown("/srv/moncic-ci/source", self.user.user_id, self.user.group_id)
        os.chown(workdir, self.user.user_id, self.user.group_id)

        # Disable reindexing of manpages during installation of build-dependencies
        run(["debconf-set-selections"], input="man-db man-db/auto-update boolean false\n", text=True)

        # Build source package
        srcinfo = get_source_info()

        if os.path.exists("debian/gbp.conf"):
            self.build_source_gbp(srcinfo, workdir)
        else:
            self.build_source_plain(srcinfo, workdir)

        with cd(workdir):
            run(["dpkg-source", "-x", srcinfo.dsc_fname])

            # Find the newly created build directory
            with os.scandir(".") as it:
                for de in it:
                    if de.is_dir():
                        builddir = de.path
                        break
                else:
                    builddir = None

            with cd(builddir):
                # Install build dependencies
                env = dict(os.environ)
                env.update(DEBIAN_FRONTEND="noninteractive")
                run(apt_get_cmd("build-dep", "./"), env=env)

                # Build dependencies are installed, we don't need internet
                # anymore: Debian packages are required to build without
                # network access
                setns.unshare(setns.CLONE_NEWNET)

                # But we do need a working loopback
                run(["ip", "link", "set", "dev", "lo", "up"])

                # Build
                # Use unshare to disable networking
                run(["dpkg-buildpackage", "--no-sign"])

            # Collect artifacts
            artifacts_dir = "/srv/artifacts"
            if os.path.isdir(artifacts_dir):
                shutil.rmtree(artifacts_dir)
            os.makedirs(artifacts_dir)

            def collect(path: str):
                log.info("Found artifact %s", path)
                link_or_copy(path, artifacts_dir)

            collect(srcinfo.changes_fname)
            for fname in get_file_list(srcinfo.changes_fname):
                collect(fname)

    def collect_artifacts(self, container: Container, destdir: str):
        user = UserConfig.from_sudoer()
        with os.scandir(os.path.join(container.get_root(), "srv", "artifacts")) as it:
            for de in it:
                if de.is_file():
                    log.info("Copying %s to %s", de.name, destdir)
                    link_or_copy(de.path, destdir, user=user)
