from __future__ import annotations

import logging
import lzma
import os
import re
import shlex
import shutil
import subprocess
from configparser import ConfigParser
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator, Optional, Sequence, Type, cast

import git

from .. import context
from ..build.utils import link_or_copy
from ..exceptions import Fail
from ..utils.guest import guest_only, host_only
from ..utils.run import run
from .source import (URL, InputSource, LocalDir, LocalFile, LocalGit, Source,
                     register)

if TYPE_CHECKING:
    from ..build import Build, Builder
    from ..container import Container

log = logging.getLogger(__name__)

re_debchangelog_head = re.compile(r"^(?P<name>\S+) \((?:[^:]+:)?(?P<tar_version>[^)-]+)(?:[^)]+)?\)")


class DebianSource(Source):
    @classmethod
    def list_build_options(cls) -> Generator[tuple[str, str], None, None]:
        yield from super().list_build_options()
        yield "build_profile", "space-separate list of Debian build profile to pass as DEB_BUILD_PROFILE"

    def get_build_class(self) -> Type["Build"]:
        from ..build.debian import Debian
        return Debian


@dataclass
class DebianDirMixin(Source):
    """
    Plain Debian source directory
    """
    tarball_filename: Optional[str] = None
    tarball_source: Optional[str] = None

    @host_only
    def _find_tarball(self, container: Container, search_dirs: Sequence[str] = ()) -> None:
        """
        Find the Debian tarball and copy it to the source directory in the container
        """
        tarball_search_dirs = []
        tarball_search_dirs.extend(search_dirs)
        if (artifacts_dir := context.moncic.get().config.build_artifacts_dir):
            tarball_search_dirs.append(artifacts_dir)

        with open(os.path.join(self.host_path, "debian", "changelog"), "rt") as fd:
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


class DebianGitSource(DebianSource):
    """
    Debian sources from a git repository
    """
    # Redefine specialized as LocalGit
    source: LocalGit

    @classmethod
    def detect(cls, builder: Builder, source: LocalGit) -> "DebianGitSource":
        if not os.path.isdir(os.path.join(source.repo.working_dir, "debian")):
            # There is no debian/directory, the current branch is upstream
            return DebianGBPTestUpstream._create_from_repo(builder, source)

        if not os.path.exists(os.path.join(source.repo.working_dir, "debian", "gbp.conf")):
            return DebianPlainGit._create_from_repo(builder, source)

        if source.repo.head.commit.hexsha in [t.commit.hexsha for t in source.repo.tags]:
            # If branch to build is a tag, build a release from it
            return DebianGBPRelease._create_from_repo(builder, source)
        else:
            # There is a debian/ directory, find upstream from gbp.conf
            return DebianGBPTestDebian._create_from_repo(builder, source)

    @classmethod
    def create(cls, builder: Builder, source: InputSource) -> "DebianGitSource":
        if isinstance(source, LocalGit):
            return cls(source.source, source.repo.working_dir)
        elif isinstance(source, URL):
            return cls.create(builder, source.clone(builder))
        else:
            raise RuntimeError(
                    f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}")


@register
@dataclass
class DebianPlainGit(DebianDirMixin, DebianGitSource):
    """
    Debian git working directory that does not use git-buildpackage.

    If no tarball can be found, one is generated with `git archive`
    """
    NAME = "debian-git-plain"

    @classmethod
    def _create_from_repo(cls, builder: Builder, source: LocalGit) -> "DebianPlainGit":
        if not source.copy:
            log.info(
                    "%s: cloning repository to avoid building a potentially dirty working directory",
                    source.repo.working_dir)
            source = source.clone(builder)

        return cls(source, source.repo.working_dir)

    @host_only
    def gather_sources_from_host(self, container: Container) -> None:
        """
        Gather needed source files from the host system and copy them to the
        guest
        """
        super().gather_sources_from_host(container)

        tarball_search_dirs = []
        if self.source.orig_path is not None:
            tarball_search_dirs.append(os.path.dirname(self.source.orig_path))
        self._find_tarball(container, tarball_search_dirs)
        if self.tarball_source is None:
            self.build_orig_tarball(container)

    @host_only
    def build_orig_tarball(self, container: Container):
        """
        Make sure srcinfo.tar_fname exists.

        This function is run from a clean source directory
        """
        dest_tarball = os.path.join(container.get_root(), "srv", "moncic-ci", "source", self.tarball_filename)

        # This is a last-resort measure, trying to build an approximation of an
        # upstream tarball when none was found
        log.info("Building tarball from source directory")
        cmd = ["git", "archive", "HEAD", ".", ":(exclude)debian"]
        log.info("Run: %s", " ".join(shlex.quote(x) for x in cmd))
        proc = subprocess.Popen(cmd, cwd=self.guest_path, stdout=subprocess.PIPE)
        with lzma.open(dest_tarball, "wb") as out:
            shutil.copyfileobj(proc.stdout, out)
        if proc.wait() != 0:
            raise RuntimeError(f"git archive exited with error code {proc.returncode}")

        self.tarball_source = "[git archive HEAD . :(exclude)debian]"

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

    @classmethod
    def ensure_local_branch_exists(cls, repo: git.Repo, branch: str):
        """
        Make sure the upstream branch exists as a local branch.

        Cloning a repository only creates one local branch for the active
        branch, and all other branches remain as origin/*

        This methods creates a local branch for the given origin/ branch
        """
        # Make a local branch for the upstream branch in gbp.conf, if it
        # does not already exist

        # Not sure how to fit the type of gitrepo.branches here, but it behaves
        # like a list of strings
        if branch not in cast(list[str], repo.branches):
            remote = repo.remotes["origin"]
            remote_branch = remote.refs[branch]
            repo.create_head(branch, remote_branch)

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

    We can attempt to build a source package by looking for a gbp-buildpackage
    branch, and merging the current upstream branch into it
    """
    NAME = "debian-gbp-upstream"

    @classmethod
    def _create_from_repo(cls, builder: Builder, source: LocalGit) -> "DebianGBPTestUpstream":
        # find the right debian branch
        candidate_branches = builder.system.distro.get_gbp_branches()
        for branch in candidate_branches:
            if source.find_branch(branch) is not None:
                break
        else:
            raise Fail(f"Packaging branch not found for distribution '{builder.system.distro}'."
                       f" Tried: {', '.join(candidate_branches)} ")

        # TODO: find common ancestor between current and packaging, and merge
        #       packaging branch from that?

        # If we are still working on an uncloned repository, create a temporary
        # clone to avoid mangling it
        if not source.copy:
            log.info("%s: cloning repository to avoid mangling the original version", source.repo.working_dir)
            source = source.clone(builder)

        # Make a temporary merge of active_branch on the debian branch
        log.info("merge packaging branch %s for test build", branch)
        active_branch = source.repo.active_branch.name
        if active_branch is None:
            log.info("repository is in detached head state, creating a 'moncic-ci' working branch from it")
            run(["git", "checkout", "-b", "moncic-ci"], cwd=source.repo.working_dir)
            active_branch = "moncic-ci"
        run(["git", "checkout", "--quiet", branch], cwd=source.repo.working_dir)
        run(["git", "-c", "user.email=moncic-ci@example.org", "-c",
             "user.name=Moncic-CI", "merge", active_branch, "--quiet", "-m", "CI merge"], cwd=source.repo.working_dir)

        res = cls(source, source.repo.working_dir)
        res.gbp_args.append("--git-upstream-tree=branch")
        res.gbp_args.append("--git-upstream-branch=" + active_branch)
        return res


@register
@dataclass
class DebianGBPRelease(DebianGBP):
    """
    Debian git working directory checked out to a tagged release branch.
    """
    NAME = "debian-gbp-release"

    @classmethod
    def _create_from_repo(cls, builder: Builder, source: LocalGit) -> "DebianGBPRelease":
        # TODO: check that debian/changelog is not UNRELEASED
        # The current directory is already the right source directory
        res = cls(source, source.repo.working_dir)
        res.gbp_args.append("--git-upstream-tree=tag")
        return res


@register
@dataclass
class DebianGBPTestDebian(DebianGBP):
    """
    Debian git working directory checked out to an untagged Debian branch.
    """
    NAME = "debian-gbp-test"

    @classmethod
    def _create_from_repo(cls, builder: Builder, source: LocalGit) -> "DebianGBPTestDebian":
        # Read the upstream branch to use from gbp.conf
        upstream_branch = cls.read_upstream_branch(source.repo)
        if upstream_branch is None:
            raise RuntimeError("Cannot read upstream branch from debian/gbp.conf")

        # If we are still working on an uncloned repository, create a temporary
        # clone to avoid mangling it
        if not source.copy:
            log.info("%s: cloning repository to avoid mangling the original version", source.repo.working_dir)
            source = source.clone(builder)

        cls.ensure_local_branch_exists(source.repo, upstream_branch)

        # Merge the upstream branch into the debian branch
        log.info("merge upstream branch %s into build branch", upstream_branch)
        run(["git", "-c", "user.email=moncic-ci@example.org", "-c",
             "user.name=Moncic-CI", "merge", upstream_branch, "--quiet", "-m", "CI merge"], cwd=source.repo.working_dir)

        res = cls(source, source.repo.working_dir)
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
    def create(cls, builder: Builder, source: InputSource) -> "DebianSourceDir":
        if isinstance(source, LocalDir):
            return cls(source, source.path)
        else:
            raise RuntimeError(
                    f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}")

    @classmethod
    def _create_from_dir(cls, builder: Builder, source: LocalDir) -> "DebianSourceDir":
        return cls(source, source.path)

    @host_only
    def gather_sources_from_host(self, container: Container) -> None:
        """
        Gather needed source files from the host system and copy them to the
        guest
        """
        super().gather_sources_from_host(container)

        tarball_search_dirs = [os.path.dirname(self.source.path)]
        self._find_tarball(container, tarball_search_dirs)

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
    def create(cls, builder: Builder, source: InputSource) -> "DebianDsc":
        if isinstance(source, LocalFile):
            return cls(source, source.path)
        else:
            raise RuntimeError(
                    f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}")

    @classmethod
    def _create_from_file(cls, builder: Builder, source: LocalFile) -> "DebianDsc":
        return cls(source, source.path)

    @host_only
    def gather_sources_from_host(self, container: Container) -> None:
        """
        Gather needed source files from the host system and copy them to the
        guest
        """
        super().gather_sources_from_host(container)

        re_files = re.compile(r"^Files:\s*$")
        re_file = re.compile(r"^\s+\S+\s+\d+\s+(\S+)\s*$")

        # Parse .dsc to get the list of assets
        file_list = [os.path.basename(self.host_path)]
        with open(self.host_path, "rt") as fd:
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
        srcdir = os.path.dirname(self.host_path)
        dstdir = os.path.join(container.get_root(), "srv", "moncic-ci", "source")
        for fname in file_list:
            link_or_copy(os.path.join(srcdir, fname), dstdir)

        self.guest_path = os.path.join("/srv/moncic-ci/source", os.path.basename(self.host_path))

    @guest_only
    def build_source_package(self) -> str:
        """
        Build a source package in /srv/moncic-ci/source returning the name of
        the main file of the source package fileset
        """
        return self.guest_path
