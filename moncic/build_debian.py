from __future__ import annotations

import logging
import os
import shutil
import tempfile
from configparser import ConfigParser
from typing import TYPE_CHECKING, List, NamedTuple, Optional

import git

from . import setns
from .build import Builder, link_or_copy, run
from .deb import apt_get_cmd
from .runner import UserConfig
from .utils import cd

if TYPE_CHECKING:
    from .container import Container, System

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
    def create(cls, system: System, srcdir: str) -> Builder:
        if (os.path.isdir(os.path.join(srcdir, "debian")) and not
                os.path.exists(os.path.join(srcdir, "debian", "gbp.conf"))):
            return DebianPlain.create(system, srcdir)
        return DebianGBP.create(system, srcdir)

    def build_source(self, workdir: str) -> SourceInfo:
        raise NotImplementedError(f"{self.__class__.__name__} not implemented")

    def build_in_container(self, workdir: str) -> Optional[int]:
        # TODO:
        # - inject dependency packages in a private apt repo if required
        #    - or export a local apt repo readonly

        os.chown("/srv/moncic-ci/source", self.user.user_id, self.user.group_id)
        os.chown(workdir, self.user.user_id, self.user.group_id)

        # Disable reindexing of manpages during installation of build-dependencies
        run(["debconf-set-selections"], input="man-db man-db/auto-update boolean false\n", text=True)

        # Build source package
        srcinfo = self.build_source(workdir)

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


class DebianPlain(Debian):
    """
    Build debian packages using the debian/ directory in the current branch
    """
    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

    def build_source(self, srcinfo: SourceInfo, workdir: str) -> SourceInfo:
        srcinfo = get_source_info()

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

        return srcinfo


class DebianGBP(Debian):
    """
    Build Debian packages using git-buildpackage
    """
    @classmethod
    def read_upstream_branch(cls) -> Optional[str]:
        """
        Read the upstream branch from gbp.conf

        Return None if gbp.conf does not exists or it does not specify an upstream branch
        """
        cfg = ConfigParser()
        cfg.read([os.path.join("debian", "gbp.conf")])
        return cfg.get("DEFAULT", "upstream-branch", fallback=None)

    @classmethod
    def ensure_local_branch_exists(cls, branch: str):
        """
        Make sure the upstream branch exists as a local branch.

        Cloning a repository only creates one local branch for the active
        branch, and all other branches remain as origin/*

        This methods creates a local branch for the given origin/ branch
        """
        # Make a local branch for the upstream branch in gbp.conf, if it
        # does not already exist
        gitrepo = git.Repo(".")
        if branch not in gitrepo.branches:
            remote = gitrepo.remotes["origin"]
            remote_branch = remote.refs[branch]
            gitrepo.create_head(branch, remote_branch)

    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        repo = git.Repo(srcdir)
        if repo.head.commit.hexsha in [t.commit.hexsha for t in repo.tags]:
            if os.path.isdir(os.path.join(srcdir, "debian")):
                # If branch to build is a tag, build a release from it
                return DebianGBPRelease.create(system, srcdir)
            else:
                # There is no debian/directory, the current branch is upstream
                return DebianGBPCIFromUpstream.create(system, srcdir)
        else:
            if os.path.isdir(os.path.join(srcdir, "debian")):
                # There is a debian/ directory, find upstream from gbp.conf
                return DebianGBPCIAutoUpstream.create(system, srcdir)
            else:
                # There is no debian/directory, the current branch is upstream
                return DebianGBPCIFromUpstream.create(system, srcdir)


class DebianGBPRelease(DebianGBP):
    """
    Build Debian packages using git-buildpackage and its configuration in the
    current branch
    """
    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

    def build_source(self, workdir: str) -> SourceInfo:
        with self.system.images.session.moncic.privs.user():
            with open(os.path.expanduser("~/.gbp.conf"), "wt") as fd:
                fd.write(f"[DEFAULT]\nexport-dir={workdir}\n")
                fd.flush()
            run(["gbp", "buildpackage", "--git-ignore-new",
                 "--git-upstream-tree=tag", "-d", "-S", "--no-sign",
                 "--no-pre-clean"])

            return get_source_info()


class DebianGBPCIFromUpstream(DebianGBP):
    """
    Build Debian packges using the current directory as upstream, and the
    packaging branch inferred from the System distribution
    """
    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

    def build_source(self, workdir: str):
        # TODO: find the right debian branch
        # TODO: make a temporary merge of active_branch on the debian branch
        # TODO: override gbp using --git-upstream-branch=<active_branch>
        raise NotImplementedError("issue #63")
        # with self.system.images.session.moncic.privs.user():
        #     with open(os.path.expanduser("~/.gbp.conf"), "wt") as fd:
        #         fd.write(f"[DEFAULT]\nexport-dir={workdir}\n")
        #         fd.flush()
        #     run(["gbp", "buildpackage", "--git-ignore-new",
        #          "--git-upstream-tree=branch", "-d", "-S", "--no-sign",
        #          "--no-pre-clean"])

        # return get_source_info()


class DebianGBPCIAutoUpstream(DebianGBP):
    """
    Build Debian packges using the current directory as the packagingbranch,
    and the upstream branch as configured in gbp.conf
    """
    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

    def build_source(self, workdir: str):
        with self.system.images.session.moncic.privs.user():
            # Read the upstream branch to use from gbp.conf
            upstream_branch = self.read_upstream_branch()
            if upstream_branch is None:
                raise RuntimeError("Cannot read upstream branch from debian/gbp.conf")

            self.ensure_local_branch_exists(upstream_branch)

            # Merge the upstream branch into the debian branch
            run(["git", "-c", "user.email=moncic-ci@example.org", "-c",
                 "user.name=Moncic-CI", "merge", upstream_branch])

            with open(os.path.expanduser("~/.gbp.conf"), "wt") as fd:
                fd.write(f"[DEFAULT]\nexport-dir={workdir}\n")
                fd.flush()
            run(["gbp", "buildpackage", "--git-ignore-new",
                 "--git-upstream-tree=branch", "-d", "-S", "--no-sign",
                 "--no-pre-clean"])

            return get_source_info()
