from __future__ import annotations

import logging
import lzma
import os
import re
import shutil
import subprocess
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence, Type

import git

from .. import context, lint
from ..build.utils import link_or_copy
from ..exceptions import Fail
from ..utils.guest import guest_only, host_only
from ..utils.run import log_run, run
from .inputsource import URL, InputSource, LocalDir, LocalFile, LocalGit
from .source import Source, register, GitCommitInfo, GitSource

if TYPE_CHECKING:
    from ..build import Build
    from ..container import Container, System
    from ..distro import Distro

log = logging.getLogger(__name__)

re_debchangelog_head = re.compile(r"^(?P<name>\S+) \((?:[^:]+:)?(?P<tar_version>[^)-]+)(?:[^)]+)?\)")


class DebianSource(Source):
    def get_build_class(self) -> Type["Build"]:
        from ..build.debian import Debian
        return Debian

    def get_linter_class(self) -> Type["lint.Linter"]:
        return lint.DebianLinter

    def find_versions(self, system: System) -> dict[str, str]:
        versions = super().find_versions(system)

        changelog = self.host_path / "debian" / "changelog"

        re_changelog = re.compile(r"\S+\s+\(([^)]+)\)")

        try:
            for line in changelog.read_text().splitlines():
                if (mo := re_changelog.match(line)):
                    debversion = mo.group(1)
                    if "-" in debversion:
                        upstream, release = debversion.split("-")
                    else:
                        upstream, release = debversion, None
                    versions["debian-upstream"] = upstream
                    if release is not None:
                        versions["debian-release"] = upstream + "-" + release
                    break
        except FileNotFoundError:
            pass

        return versions


@dataclass
class DebianDirMixin(Source):
    """
    Plain Debian source directory
    """
    tarball_filename: Optional[str] = None
    tarball_source: Optional[str] = None

    @host_only
    def _find_tarball(self, build: Build, container: Container, search_dirs: Sequence[str] = ()) -> None:
        """
        Find the Debian tarball and copy it to the source directory in the container
        """
        tarball_search_dirs: list[str] = []
        tarball_search_dirs.extend(search_dirs)
        if (artifacts_dir := build.artifacts_dir):
            tarball_search_dirs.append(artifacts_dir)

        with (self.host_path / "debian" / "changelog").open("rt") as fd:
            if (mo := re_debchangelog_head.match(next(fd))):
                src_name = mo.group("name")
                tar_version = mo.group("tar_version")
                tarball_match = f"{src_name}_{tar_version}.orig.tar"
            else:
                raise RuntimeError("Unparsable debian/changelog")

        self.tarball_source = None
        for path in tarball_search_dirs:
            with os.scandir(path) as it:
                for de in it:
                    if de.name.startswith(tarball_match):
                        self.tarball_filename = de.name
                        self.tarball_source = de.path
                        break
            if self.tarball_source:
                log.info("Found existing source tarball %s", self.tarball_source)
                container_root = container.get_root()
                link_or_copy(self.tarball_source, os.path.join(container_root, "srv", "moncic-ci", "source"))
                break
            else:
                # Default to .xz compression
                self.tarball_filename = tarball_match + ".xz"


class DebianGitSource(DebianSource, GitSource):
    """
    Debian sources from a git repository
    """

    @classmethod
    def detect(cls, distro: Distro, source: LocalGit) -> "DebianGitSource":
        if source.repo.working_dir is None:
            raise RuntimeError(f"{source} has no working directory")
        debian_path = Path(source.repo.working_dir) / "debian"
        if not (debian_path / "changelog").exists():
            # There is no debian/directory, the current branch is upstream
            return DebianGBPTestUpstream._create_from_repo(distro, source)

        if not (debian_path / "gbp.conf").exists():
            return DebianPlainGit._create_from_repo(distro, source)

        if source.repo.head.commit.hexsha in [t.commit.hexsha for t in source.repo.tags]:
            # If branch to build is a tag, build a release from it
            return DebianGBPRelease._create_from_repo(distro, source)
        else:
            # There is a debian/ directory, find upstream from gbp.conf
            return DebianGBPTestDebian._create_from_repo(distro, source)

    @classmethod
    def create(cls, distro: Distro, source: InputSource) -> "DebianGitSource":
        if isinstance(source, LocalGit):
            return cls._create_from_repo(distro, source)
        elif isinstance(source, URL):
            return cls._create_from_repo(distro, source.clone())
        else:
            raise RuntimeError(
                    f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}")


@register
@dataclass
class DebianPlainGit(DebianDirMixin, DebianGitSource):
    """
    Debian git working directory that does not use git-buildpackage.

    This is autoselected if the `debian/` directory exists, but there is no
    `debian/gbp.conf`.

    An upstream `orig.tar.gz` tarball is searched on `..` and on the artifacts
    directory, and used if found.

    If no existing upstream tarball is found, one is generated using
    `git archive HEAD . ":(exclude)debian"`, as a last-resort measure.
    """
    NAME = "debian-git-plain"

    @classmethod
    def _create_from_repo(cls, distro: Distro, source: LocalGit) -> "DebianPlainGit":
        if not source.copy:
            log.info(
                    "%s: cloning repository to avoid building a potentially dirty working directory",
                    source.repo.working_dir)
            source = source.clone()

        if source.repo.working_dir is None:
            raise RuntimeError(f"{source} repository has no working directory")

        res = cls(source, Path(source.repo.working_dir))
        res.add_trace_log("git", "clone", "-b", source.repo.active_branch.name, source.source)
        return res

    @host_only
    def gather_sources_from_host(self, build: Build, container: Container) -> None:
        """
        Gather needed source files from the host system and copy them to the
        guest
        """
        super().gather_sources_from_host(build, container)

        tarball_search_dirs = []
        if self.source.orig_path is not None:
            tarball_search_dirs.append(os.path.dirname(self.source.orig_path))
        self._find_tarball(build, container, tarball_search_dirs)
        if self.tarball_source is None:
            self.build_orig_tarball(container)

    @host_only
    def build_orig_tarball(self, container: Container):
        """
        Make sure srcinfo.tar_fname exists.

        This function is run from a clean source directory
        """
        if self.tarball_filename is None:
            raise RuntimeError("tarball file not found")
        source_dir = os.path.join(container.get_root(), "srv", "moncic-ci", "source")
        source_stat = os.stat(source_dir)
        dest_tarball = os.path.join(source_dir, self.tarball_filename)
        with lzma.open(dest_tarball, "wb") as out:
            with context.moncic.get().privs.user():

                # This is a last-resort measure, trying to build an approximation of an
                # upstream tarball when none was found
                log.info("Building tarball from source directory")
                cmd = ["git", "archive", "HEAD", ".", ":(exclude)debian"]
                log_run(cmd, cwd=self.source.path)
                proc = subprocess.Popen(cmd, cwd=self.source.path, stdout=subprocess.PIPE)
                shutil.copyfileobj(proc.stdout, out)
                if proc.wait() != 0:
                    raise RuntimeError(f"git archive exited with error code {proc.returncode}")

                self.tarball_source = "[git archive HEAD . :(exclude)debian]"
        os.chown(dest_tarball, source_stat.st_uid, source_stat.st_gid)

    @guest_only
    def build_source_package(self) -> str:
        """
        Build a source package in /srv/moncic-ci/source returning the name of
        the main file of the source package fileset
        """
        # Uses --no-pre-clean to avoid requiring build-deps to be installed at
        # this stage
        run(["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"], cwd=self.guest_path)

        for fn in os.listdir("/srv/moncic-ci/source"):
            if fn.endswith(".dsc"):
                return os.path.join("/srv/moncic-ci/source", fn)

        raise RuntimeError(".dsc file not found after dpkg-buildpackage -S")


@dataclass
class DebianGBP(DebianGitSource):
    """
    Debian git working directory with a git-buildpackage setup
    """
    gbp_args: list[str] = field(default_factory=list)

    @classmethod
    def read_upstream_branch(cls, repo: git.Repo) -> Optional[str]:
        """
        Read the upstream branch from gbp.conf

        Return None if gbp.conf does not exists or it does not specify an upstream branch
        """
        cfg = ConfigParser()
        cfg.read([os.path.join(repo.working_dir, "debian", "gbp.conf")])
        return cfg.get("DEFAULT", "upstream-branch", fallback=None)

    @guest_only
    def build_source_package(self) -> str:
        """
        Build a source package in /srv/moncic-ci/source returning the name of
        the main file of the source package fileset
        """
        cmd = ["gbp", "buildpackage", "--git-ignore-new",
               "-d", "-S", "--no-sign", "--no-pre-clean"]
        cmd += self.gbp_args
        run(cmd, cwd=self.guest_path)

        for fn in os.listdir("/srv/moncic-ci/source"):
            if fn.endswith(".dsc"):
                return os.path.join("/srv/moncic-ci/source", fn)

        raise RuntimeError(".dsc file not found after gbp buildpackage -S")


@register
@dataclass
class DebianGBPTestUpstream(DebianGBP):
    """
    Merge the current upstream working directory into the packaging branch for
    the build distro.

    This will look for a packaging branch corresponding to the distribution
    used by the current build image (for example, `debian/bullseye` when
    running on a Debian 11 image, or `ubuntu/jammy` when running on an Ubuntu
    22.04 image.

    It will then check it out, merge the source branch into it, and build the
    resulting package.

    This is autoselected if either:

    * the git commit being built is a git tag but does not contain a `debian/`
      directory (i.e. testing packaging of a tagged upstream branch)
    * the git commit being built is not a git tag, and does not contain a `debian/`
      directory (i.e. testing packaging of an upstream branch)
    """
    NAME = "debian-gbp-upstream"

    @classmethod
    def _create_from_repo(cls, distro: Distro, source: LocalGit) -> "DebianGBPTestUpstream":
        # find the right debian branch
        candidate_branches = distro.get_gbp_branches()
        for branch in candidate_branches:
            if source.find_branch(branch) is not None:
                break
        else:
            raise Fail(f"Packaging branch not found for distribution '{distro}'."
                       f" Tried: {', '.join(candidate_branches)} ")

        # TODO: find common ancestor between current and packaging, and merge
        #       packaging branch from that?

        # If we are still working on an uncloned repository, create a temporary
        # clone to avoid mangling it
        if not source.copy:
            log.info("%s: cloning repository to avoid mangling the original version", source.repo.working_dir)
            source = source.clone()

        res = cls(source, Path(source.repo.working_dir))

        # Make a temporary merge of active_branch on the debian branch
        log.info("merge packaging branch %s for test build", branch)
        active_branch = source.repo.active_branch.name
        if active_branch is None:
            log.info("repository is in detached head state, creating a 'moncic-ci' working branch from it")
            res.add_trace_log("git", "clone", source.source)
            cmd = ["git", "checkout", source.repo.head.commit.hexsha, "-b", "moncic-ci"]
            res.add_trace_log(*cmd)
            run(cmd, cwd=source.repo.working_dir)
            active_branch = "moncic-ci"
        else:
            res.add_trace_log("git", "clone", "-b", active_branch, source.source)

        cmd = ["git", "checkout", "--quiet", branch]
        res.add_trace_log(*cmd)
        run(cmd, cwd=source.repo.working_dir)

        cmd = ["git", "-c", "user.email=moncic-ci@example.org", "-c",
               "user.name=Moncic-CI", "merge", "--quiet", str(active_branch), "-m", "CI merge"]
        res.add_trace_log(*cmd)
        run(cmd, cwd=source.repo.working_dir)

        res.gbp_args.append("--git-upstream-tree=branch")
        res.gbp_args.append("--git-upstream-branch=" + active_branch)
        return res


@register
@dataclass
class DebianGBPRelease(DebianGBP):
    """
    Debian git working directory checked out to a tagged release branch.

    This is autoselected if the git commit being built is a git tag, and it
    contains a `debian/` directory.

    `git-buildpackage` is invoked with `--git-upstream-tree=tag`, to build the
    release version of a package.
    """
    NAME = "debian-gbp-release"

    @classmethod
    def _create_from_repo(cls, distro: Distro, source: LocalGit) -> "DebianGBPRelease":
        # TODO: check that debian/changelog is not UNRELEASED
        # The current directory is already the right source directory

        # If we are still working on an uncloned repository, create a temporary
        # clone to work on a clean one
        if not source.copy:
            log.info("%s: cloning repository to avoid mangling the original version", source.repo.working_dir)
            source = source.clone()

        res = cls(source, Path(source.repo.working_dir))
        res.gbp_args.append("--git-upstream-tree=tag")
        return res


@register
@dataclass
class DebianGBPTestDebian(DebianGBP):
    """
    Debian git working directory checked out to an untagged Debian branch.

    This is autoselected if the git commit being built is not a tag, and it
    contains a `debian/` directory.

    The upstream branch is read from `debian/gbp.conf`, and merged into the
    current branch. After which, git-buildpackage is run with
    `--git-upstream-tree=branch`.

    This is used to test the Debian packaging against its intended upstream
    branch.
    """
    NAME = "debian-gbp-test"

    @classmethod
    def _create_from_repo(cls, distro: Distro, source: LocalGit) -> "DebianGBPTestDebian":
        # Read the upstream branch to use from gbp.conf
        upstream_branch = cls.read_upstream_branch(source.repo)
        if upstream_branch is None:
            raise RuntimeError("Cannot read upstream branch from debian/gbp.conf")

        # If we are still working on an uncloned repository, create a temporary
        # clone to avoid mangling it
        if not source.copy:
            log.info("%s: cloning repository to avoid mangling the original version", source.repo.working_dir)
            source = source.clone()

        res = cls(source, Path(source.repo.working_dir))
        res.add_trace_log("git", "clone", "-b", str(source.repo.active_branch), source.source)

        # Merge the upstream branch into the debian branch
        log.info("merge upstream branch %s into build branch", upstream_branch)
        cmd = ["git", "-c", "user.email=moncic-ci@example.org", "-c", "user.name=Moncic-CI",
               "merge", upstream_branch, "--quiet", "-m", "CI merge"]
        res.add_trace_log(*cmd)
        run(cmd, cwd=source.repo.working_dir)

        res.gbp_args.append("--git-upstream-tree=branch")
        return res


@register
@dataclass
class DebianSourceDir(DebianDirMixin, DebianSource):
    """
    Unpacked debian source
    """
    NAME = "debian-dir"

    @classmethod
    def create(cls, distro: Distro, source: InputSource) -> "DebianSourceDir":
        if isinstance(source, LocalDir):
            return cls._create_from_dir(distro, source)
        else:
            raise RuntimeError(
                    f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}")

    @classmethod
    def _create_from_dir(cls, distro: Distro, source: LocalDir) -> "DebianSourceDir":
        return cls(source, Path(source.path))

    @host_only
    def gather_sources_from_host(self, build: Build, container: Container) -> None:
        """
        Gather needed source files from the host system and copy them to the
        guest
        """
        super().gather_sources_from_host(build, container)

        tarball_search_dirs = [os.path.dirname(self.source.path)]
        self._find_tarball(build, container, tarball_search_dirs)

    @guest_only
    def build_source_package(self) -> str:
        """
        Build a source package in /srv/moncic-ci/source returning the name of
        the main file of the source package fileset
        """
        # Uses --no-pre-clean to avoid requiring build-deps to be installed at
        # this stage
        run(["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"], cwd=self.guest_path)

        for fn in os.listdir("/srv/moncic-ci/source"):
            if fn.endswith(".dsc"):
                return os.path.join("/srv/moncic-ci/source", fn)

        raise RuntimeError(".dsc file not found after dpkg-buildpackage -S")


@register
@dataclass
class DebianDsc(DebianSource):
    """
    Debian source .dsc
    """
    NAME = "debian-dsc"

    @classmethod
    def create(cls, distro: Distro, source: InputSource) -> "DebianDsc":
        if isinstance(source, LocalFile):
            return cls._create_from_file(distro, source)
        else:
            raise RuntimeError(
                    f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}")

    @classmethod
    def _create_from_file(cls, distro: Distro, source: LocalFile) -> "DebianDsc":
        return cls(source, Path(source.path))

    @host_only
    def gather_sources_from_host(self, build: Build, container: Container) -> None:
        """
        Gather needed source files from the host system and copy them to the
        guest
        """
        super().gather_sources_from_host(build, container)

        re_files = re.compile(r"^Files:\s*$")
        re_file = re.compile(r"^\s+\S+\s+\d+\s+(\S+)\s*$")

        # Parse .dsc to get the list of assets
        file_list = [self.host_path.name]
        with self.host_path.open("rt") as fd:
            files_section = False
            for line in fd:
                if not files_section:
                    if re_files.match(line):
                        files_section = True
                else:
                    mo = re_file.match(line)
                    if not mo:
                        break
                    file_list.append(mo.group(1))

        # Copy .dsc and its assets to the container
        srcdir = self.host_path.parent
        dstdir = os.path.join(container.get_root(), "srv", "moncic-ci", "source")
        for fname in file_list:
            link_or_copy(srcdir / fname, dstdir)

        self.guest_path = os.path.join("/srv/moncic-ci/source", self.host_path.name)

    @guest_only
    def build_source_package(self) -> str:
        """
        Build a source package in /srv/moncic-ci/source returning the name of
        the main file of the source package fileset
        """
        return self.guest_path
