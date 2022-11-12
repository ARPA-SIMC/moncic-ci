from __future__ import annotations

import contextlib
import importlib.resources
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from configparser import ConfigParser
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator, List, NamedTuple, Optional, Type, cast

import git

from .. import setns
from ..deb import apt_get_cmd
from ..runner import UserConfig
from ..utils import cd, guest_only, host_only, run
from .analyze import Analyzer
from .base import Builder, link_or_copy

if TYPE_CHECKING:
    from .container import Container, System

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


@dataclass
class BuildInfo:
    pass


@Builder.register
class Debian(Builder):
    build_info_cls: Type[BuildInfo] = BuildInfo

    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        if (os.path.isdir(os.path.join(srcdir, "debian")) and not
                os.path.exists(os.path.join(srcdir, "debian", "gbp.conf"))):
            return DebianPlain.create(system, srcdir)
        return DebianGBP.create(system, srcdir)

    def __init__(self, system: System, srcdir: str):
        super().__init__(system, srcdir)
        # This is only set in guest systems, and after self.build_source() has
        # been called
        self.srcinfo: Optional[SourceInfo] = None

    @host_only
    def get_build_deps(self) -> List[str]:
        with self.container() as container:
            # Inject a perl script that uses libdpkg-perl to compute the dependency list
            with importlib.resources.open_binary("moncic.build", "debian-dpkg-listbuilddeps") as fdin:
                with open(os.path.join(container.get_root(), "srv", "moncic-ci", "dpkg-listbuilddeps"), "wb") as fdout:
                    shutil.copyfileobj(fdin, fdout)
                    os.fchmod(fdout.fileno(), 0o755)

            # Build run config
            run_config = container.config.run_config()

            res = container.run_callable(
                    self.get_build_deps_in_container,
                    run_config)
            res.check_returncode()

            with open(os.path.join(container.get_root(), "srv", "moncic-ci", "build", "result.json"), "rt") as fd:
                result = json.load(fd)

        return result["packages"]

    @guest_only
    def get_build_deps_in_container(self):
        build_info = self.build_info_cls()

        with self.source_directory(build_info):
            res = subprocess.run(["/srv/moncic-ci/dpkg-listbuilddeps"], stdout=subprocess.PIPE, text=True, check=True)
            # Throw away version constraints, since the package name with
            # version constraints cannot be passed to apt-get
            packages = [name.split(None, 1)[0].strip() for name in res.stdout.strip().splitlines()]
            with open("/srv/moncic-ci/build/result.json", "wt") as out:
                json.dump({"packages": packages}, out)

    @guest_only
    def build_source(self, build_info: BuildInfo):
        """
        Build the Debian source package from /srv/moncic-ci/source/<name>

        The results will be left in /srv/moncic-ci.

        This function is run as the build user
        """
        raise NotImplementedError(f"{self.__class__.__name__}.build_source not implemented")

    @guest_only
    def setup_container_guest(self):
        super().setup_container_guest()
        # Reinstantiate the module logger
        global log
        log = logging.getLogger(__name__)

        # TODO: run apt update if the apt index is older than some threshold

        # Disable reindexing of manpages during installation of build-dependencies
        run(["debconf-set-selections"], input="man-db man-db/auto-update boolean false\n", text=True)

    @guest_only
    @contextlib.contextmanager
    def source_directory(self, build_info: BuildInfo) -> Generator[None, None, None]:
        """
        Change the current directory to the one with the sources ready to be built
        """
        raise NotImplementedError(f"{self.__class__.__name__}.source_directory not implemented")

    @guest_only
    def build_in_container(self, source_only: bool = False) -> Optional[int]:
        build_info = self.build_info_cls()

        self.setup_container_guest()

        # Build source package
        with self.system.images.session.moncic.privs.user():
            self.build_source(build_info)

        if not source_only:
            self.build_binary(build_info)

        # Collect artifacts
        artifacts_dir = "/srv/artifacts"
        if os.path.isdir(artifacts_dir):
            shutil.rmtree(artifacts_dir)
        os.makedirs(artifacts_dir)

        def collect(path: str):
            log.info("Found artifact %s", path)
            link_or_copy(path, artifacts_dir)

        for path in "/srv/moncic-ci/source", "/srv/moncic-ci/build":
            with os.scandir(path) as it:
                for de in it:
                    if de.is_file():
                        collect(de.path)

        return None

    @guest_only
    def build_binary(self, build_info: BuildInfo):
        """
        Build binary packages
        """
        with cd("/srv/moncic-ci/build"):
            run(["dpkg-source", "-x", self.srcinfo.dsc_fname])

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

    @host_only
    def collect_artifacts(self, container: Container, destdir: str):
        container_root = container.get_root()
        user = UserConfig.from_sudoer()
        build_log_name: Optional[str] = None
        with os.scandir(os.path.join(container_root, "srv", "artifacts")) as it:
            for de in it:
                if de.is_file():
                    if de.name.endswith("_source.changes"):
                        build_log_name = de.name[:-15] + ".buildlog"
                    log.info("Copying %s to %s", de.name, destdir)
                    link_or_copy(de.path, destdir, user=user)

        if build_log_name is None:
            build_log_name = os.path.basename(self.srcdir) + ".buildlog"

        if os.path.exists(logfile := os.path.join(container_root, "srv", "moncic-ci", "buildlog")):
            self.log_capture_end()
            link_or_copy(
                    logfile, destdir, user=user,
                    filename=build_log_name)
            log.info("Saving build log to %s/%s", destdir, build_log_name)

    @classmethod
    def analyze(cls, analyzer: Analyzer):
        upstream_version = analyzer.upstream_version
        debian_version = Analyzer.same_values(analyzer.version_from_debian_branches)

        # Check that debian/changelog versions are in sync with upstream
        if upstream_version is not None and debian_version is not None:
            if upstream_version not in debian_version:
                analyzer.warning(f"Debian version {debian_version!r} is out of sync"
                                 f" with upstream version {upstream_version!r}")
            # if debian_version is None:
            #     analyzer.warning("Cannot univocally determine debian version")

        # Check upstream merge status of the various debian branches
        upstream_branch = analyzer.repo.references[analyzer.main_branch]
        for name, branch_name in analyzer.debian_packaging_branches.items():
            debian_branch = analyzer.repo.references[branch_name]
            if not analyzer.repo.is_ancestor(upstream_branch, debian_branch):
                analyzer.warning(f"Upstream branch {analyzer.main_branch!r} is not merged in {name!r}")

        # TODO: check tags present for one distro but not for the other
        # TODO: check that upstream tag exists if debian/changelog is not UNRELEASED


@Builder.register
class DebianPlain(Debian):
    """
    Build debian packages using the debian/ directory in the current branch
    """
    re_debchangelog_head = re.compile(r"^(?P<name>\S+) \((?:[^:]+:)?(?P<tar_version>[^)-]+)(?:[^)]+)?\)")

    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

    @host_only
    def setup_container_host(self, container: Container):
        super().setup_container_host(container)

        tarball_search_dirs = [os.path.join(self.srcdir, "..")]
        if (artifacts_dir := self.system.images.session.moncic.config.build_artifacts_dir):
            tarball_search_dirs.append(artifacts_dir)

        with open(os.path.join(self.srcdir, "debian", "changelog"), "rt") as fd:
            if (mo := self.re_debchangelog_head.match(next(fd))):
                src_name = mo.group("name")
                tar_version = mo.group("tar_version")
                tar_fname = f"{src_name}_{tar_version}.orig.tar.gz"
            else:
                raise RuntimeError("Unparsable debian/changelog")

        found = None
        for path in tarball_search_dirs:
            with os.scandir(path) as it:
                for de in it:
                    if de.name == tar_fname:
                        found = de.path
                        break
            if found:
                log.info("Found existing source tarball %s", found)
                container_root = container.get_root()
                link_or_copy(found, os.path.join(container_root, "srv", "moncic-ci", "source"))
                break

    @guest_only
    def build_orig_tarball(self):
        """
        Make sure srcinfo.tar_fname exists.

        This function is run from a clean source directory
        """
        dest_tarball = os.path.join("..", self.srcinfo.tar_fname)
        if os.path.exists(orig_tarball := os.path.join("/srv", "moncic-ci", "source", self.srcinfo.tar_fname)):
            link_or_copy(orig_tarball, "..")
            return

        # This is a last-resort measure, trying to build an approximation of an
        # upstream tarball when none was found
        log.info("Building tarball from source directory")
        run(["git", "archive", f"--output={dest_tarball}", "HEAD", ".", ":(exclude)debian"])

    @guest_only
    @contextlib.contextmanager
    def source_directory(self, build_info: BuildInfo) -> Generator[None, None, None]:
        with tempfile.TemporaryDirectory(dir="/srv/moncic-ci/build") as clean_src:
            # Make a clean clone to avoid potentially building from a dirty
            # working directory
            run(["git", "clone", ".", clean_src])

            with cd(clean_src):
                yield

    @guest_only
    def build_source(self, build_info: BuildInfo):
        with self.source_directory(build_info):
            self.srcinfo = get_source_info()

            self.build_orig_tarball()

            # Uses --no-pre-clean to avoid requiring build-deps to be installed at
            # this stage
            run(["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"])

            # No need to copy .dsc and its assets to the work
            # directory, since we're building on a temporary subdir inside it


@dataclass
class GBPBuildInfo(BuildInfo):
    """
    BuildInfo class with gbp-buildpackage specific fields
    """
    gbp_args: List[str] = field(default_factory=list)


@Builder.register
class DebianGBP(Debian):
    """
    Build Debian packages using git-buildpackage
    """
    build_info_cls = GBPBuildInfo

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
        # Not sure how to fit the type of gitrepo.branches here, but it behaves
        # like a list of strings
        if branch not in cast(list[str], gitrepo.branches):
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
                return DebianGBPTestUpstream.create(system, srcdir)
        else:
            if os.path.isdir(os.path.join(srcdir, "debian")):
                # There is a debian/ directory, find upstream from gbp.conf
                return DebianGBPTestDebian.create(system, srcdir)
            else:
                # There is no debian/directory, the current branch is upstream
                return DebianGBPTestUpstream.create(system, srcdir)

    @guest_only
    def setup_container_guest(self):
        super().setup_container_guest()
        # Set up git-buildpackage before the build
        with self.system.images.session.moncic.privs.user():
            with open(os.path.expanduser("~/.gbp.conf"), "wt") as fd:
                fd.write("[DEFAULT]\nexport-dir=/srv/moncic-ci/build\n")
                fd.flush()


@Builder.register
class DebianGBPRelease(DebianGBP):
    """
    Build Debian packages using git-buildpackage and its configuration in the
    current branch
    """
    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

    @guest_only
    @contextlib.contextmanager
    def source_directory(self, build_info: GBPBuildInfo) -> Generator[None, None, None]:
        build_info.gbp_args.append("--git-upstream-tree=tag")
        # The current directory is already the right source directory
        yield

    def build_source(self, build_info: GBPBuildInfo):
        with self.source_directory(build_info):
            cmd = ["gbp", "buildpackage", "--git-ignore-new",
                   "-d", "-S", "--no-sign", "--no-pre-clean"]
            cmd += build_info.gbp_args
            run(cmd)

            self.srcinfo = get_source_info()


@Builder.register
class DebianGBPTestUpstream(DebianGBP):
    """
    Build Debian packges using the current directory as upstream, and the
    packaging branch inferred from the System distribution
    """
    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

    @guest_only
    @contextlib.contextmanager
    def source_directory(self, build_info: GBPBuildInfo) -> Generator[None, None, None]:
        # find the right debian branch
        branch = self.system.distro.get_gbp_branch()
        repo = git.Repo(".")
        origin = repo.remotes["origin"]
        if branch not in origin.refs:
            raise RuntimeError(f"Packaging branch {branch!r} not found for distribution '{self.system.distro}'")

        # TODO: find common ancestor between current and packaging, and merge
        #       packaging branch from that?

        # Make a temporary merge of active_branch on the debian branch
        log.info("merge packaging branch %s for test build", branch)
        active_branch = repo.active_branch.name
        if active_branch is None:
            log.info("repository is in detached head state, creating a 'moncic-ci' working branch from it")
            run(["git", "checkout", "-b", "moncic-ci"])
            active_branch = "moncic-ci"
        run(["git", "checkout", branch])
        run(["git", "-c", "user.email=moncic-ci@example.org", "-c",
             "user.name=Moncic-CI", "merge", active_branch])

        build_info.gbp_args.append("--git-upstream-tree=branch")
        build_info.gbp_args.append("--git-upstream-branch=" + active_branch)

        # Use the current directory
        yield

    @guest_only
    def build_source(self, build_info: GBPBuildInfo):
        with self.source_directory(build_info):
            cmd = ["gbp", "buildpackage", "--git-ignore-new",
                   "-d", "-S", "--no-sign", "--no-pre-clean"]
            cmd += build_info.gbp_args
            run(cmd)

            self.srcinfo = get_source_info()


@Builder.register
class DebianGBPTestDebian(DebianGBP):
    """
    Build Debian packges using the current directory as the packaging branch,
    and the upstream branch as configured in gbp.conf
    """
    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

    @guest_only
    @contextlib.contextmanager
    def source_directory(self, build_info: GBPBuildInfo) -> Generator[None, None, None]:
        # Read the upstream branch to use from gbp.conf
        upstream_branch = self.read_upstream_branch()
        if upstream_branch is None:
            raise RuntimeError("Cannot read upstream branch from debian/gbp.conf")

        self.ensure_local_branch_exists(upstream_branch)

        # Merge the upstream branch into the debian branch
        log.info("merge upstream branch %s into build branch", upstream_branch)
        run(["git", "-c", "user.email=moncic-ci@example.org", "-c",
             "user.name=Moncic-CI", "merge", upstream_branch])

        build_info.gbp_args.append("--git-upstream-tree=branch")

        # Use the current directory
        yield

    @guest_only
    def build_source(self, build_info: GBPBuildInfo):
        with self.source_directory(build_info):
            cmd = ["gbp", "buildpackage", "--git-ignore-new",
                   "-d", "-S", "--no-sign", "--no-pre-clean"]
            cmd += build_info.gbp_args
            run(cmd)

            self.srcinfo = get_source_info()
