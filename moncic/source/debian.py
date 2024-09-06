from __future__ import annotations

import abc
import logging
import lzma
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, cast

import git

from .. import context, lint
from ..build.utils import link_or_copy
from ..exceptions import Fail
from ..utils.guest import guest_only, host_only
from ..utils.run import log_run, run
from .local import Dir, Git, File
from .source import Source

if TYPE_CHECKING:
    from ..build import Build
    from ..container import Container, System
    from ..distro import Distro

log = logging.getLogger(__name__)

re_debchangelog_head = re.compile(r"^(?P<name>\S+) \((?:[^:]+:)?(?P<version>[^)]+)\)")


class SourceInfo(NamedTuple):
    """
    Information about a Debian source package
    """

    #: Source package name
    name: str
    #: Source package version
    version: str
    #: Name of the source .dsc file
    dsc_filename: str
    #: Name of the source tarball, without extension
    tar_stem: str

    @classmethod
    def create_from_dir(self, path: Path) -> "SourceInfo":
        """
        Get source information from an unpacked Debian source
        """
        with (path / "debian" / "changelog").open() as fd:
            if mo := re_debchangelog_head.match(next(fd)):
                name = mo.group("name")
                version = mo.group("version")
            else:
                raise Fail("Unparsable debian/changelog")

        version_dsc = version.split(":", 1)[1] if ":" in version else version
        if "-" in version_dsc:
            upstream_version = version_dsc.split("-", 1)[0]
            tar_stem = f"{name}_{upstream_version}.orig.tar"
        else:
            tar_stem = f"{name}_{version_dsc}.tar"

        return SourceInfo(name=name, version=version, dsc_filename=f"{name}_{version_dsc}.dsc", tar_stem=tar_stem)

    def find_tarball(self, search_dirs: Sequence[Path] = ()) -> Path | None:
        """
        Find the Debian upstream or source tarball
        """
        for path in search_dirs:
            for sub in path.iterdir():
                if sub.name.startswith(self.tar_stem):
                    log.info("Found existing source tarball %s", sub)
                    return sub

        return None


class DebianSource(Source, abc.ABC):
    """
    Base class for Debian source packages
    """

    @classmethod
    def _find_tarball_for_unpacked_sources(cls, path: Path, source_info: SourceInfo) -> Path:
        # TODO: if artifacts_dir := build.artifacts_dir:
        # TODO:     tarball_search_dirs.append(artifacts_dir)
        tarball = source_info.find_tarball([path.parent])
        if tarball is None:
            raise Fail(f"Tarball {source_info.tar_stem}.* not found")
        return tarball

    @classmethod
    def create_from_file(cls, parent: File) -> "DebianDsc":
        if parent.path.suffix == ".dsc":
            return DebianDsc(parent=parent, path=parent.path)
        else:
            raise Fail(f"{parent.path}: cannot detect source type")

    @classmethod
    def create_from_dir(cls, parent: Dir) -> "DebianDir":
        if not (parent.path / "debian").is_dir():
            raise Fail(f"{parent.path}: cannot detect source type")

        source_info = SourceInfo.create_from_dir(parent.path)
        tarball = cls._find_tarball_for_unpacked_sources(parent.path, source_info)
        return DebianDir(parent=parent, path=parent.path, source_info=source_info, tarball=tarball)

    @classmethod
    def create_from_git(cls, parent: Git) -> "DebianSource":
        """
        Detect the style of packaging repository.

        If the debian/directory does not exist, assume we're working on an
        upstream branch to be temporarily merged into a Debian packaging branch.

        If debian/gbp.conf does not exist, assume it's a checkout of a plain
        Debian source package that does not use gbp-buildpackage.

        If debian/gbp.conf exists, and the current commit is tagged, assume
        that we're releasing the current branch.

        If the current commit is not tagged, assume we are testing packaging
        against the current upstream and temporarily merge upstream into this
        branch.
        """
        # Switch to the right branch first, if needed
        parent = parent.get_branch()

        repo = parent.repo
        if repo.working_dir is None:
            raise RuntimeError(f"{parent.path} has no working directory")

        debian_path = parent.path / "debian"
        if not debian_path.exists() or not (debian_path / "changelog").exists():
            # There is no debian/changelog: the current branch could be
            # upstream in a gbp repository
            raise NotImplementedError("return DebianGBPTestUpstream._create_from_repo(distro, source)")

        source_info = SourceInfo.create_from_dir(parent.path)

        # Check if it's a gbp-buildpackage source
        gbp_conf_path = debian_path / "gbp.conf"
        if not gbp_conf_path.exists():
            # Not a gib-buildpackage source, build with dpkg-buildpackage
            tarball = cls._find_tarball_for_unpacked_sources(parent.path, source_info)
            # FIXME: cast not needed after Python 3.11
            return cast(
                DebianGitLegacy,
                DebianGitLegacy.derive_from_git(
                    parent,
                    source_info=source_info,
                    tarball=tarball,
                ),
            )

        # Check if we are building a tagged commit
        if parent.repo.head.commit.hexsha in [t.commit.hexsha for t in parent.repo.tags]:
            # If branch to build is a tag, build a release from it
            raise NotImplementedError("return DebianGBPRelease._create_from_repo(distro, source, debversion)")

        # There is a debian/ directory, find upstream from gbp.conf
        raise NotImplementedError("return DebianGBPTestDebian._create_from_repo(distro, source, debversion)")


#    @classmethod
#    def detect(cls, distro: Distro, source: LocalGit) -> DebianGitSource:
#        debian_path = Path(source.repo.working_dir) / "debian"
#        debian_changelog_path = debian_path / "changelog"
#


#    def get_build_class(self) -> type[Build]:
#        from ..build.debian import Debian
#
#        return Debian
#
#    def get_linter_class(self) -> type[lint.Linter]:
#        return lint.DebianLinter
#
#    def find_versions(self, system: System) -> dict[str, str]:
#        versions = super().find_versions(system)
#
#        changelog = self.host_path / "debian" / "changelog"
#
#        re_changelog = re.compile(r"\S+\s+\(([^)]+)\)")
#
#        try:
#            for line in changelog.read_text().splitlines():
#                if mo := re_changelog.match(line):
#                    debversion = mo.group(1)
#                    if "-" in debversion:
#                        upstream, release = debversion.split("-")
#                    else:
#                        upstream, release = debversion, None
#                    versions["debian-upstream"] = upstream
#                    if release is not None:
#                        versions["debian-release"] = upstream + "-" + release
#                    break
#        except FileNotFoundError:
#            pass
#
#        return versions


class DebianDsc(DebianSource, File):
    """
    Debian source .dsc
    """

    NAME = "debian-dsc"


#     @host_only
#     def gather_sources_from_host(self, build: Build, container: Container) -> None:
#         """
#         Gather needed source files from the host system and copy them to the
#         guest
#         """
#         super().gather_sources_from_host(build, container)
#
#         re_files = re.compile(r"^Files:\s*$")
#         re_file = re.compile(r"^\s+\S+\s+\d+\s+(\S+)\s*$")
#
#         # Parse .dsc to get the list of assets
#         file_list = [self.host_path.name]
#         with self.host_path.open("rt") as fd:
#             files_section = False
#             for line in fd:
#                 if not files_section:
#                     if re_files.match(line):
#                         files_section = True
#                 else:
#                     mo = re_file.match(line)
#                     if not mo:
#                         break
#                     file_list.append(mo.group(1))
#
#         # Copy .dsc and its assets to the container
#         srcdir = self.host_path.parent
#         dstdir = os.path.join(container.get_root(), "srv", "moncic-ci", "source")
#         for fname in file_list:
#             link_or_copy(srcdir / fname, dstdir)
#
#         self.guest_path = os.path.join("/srv/moncic-ci/source", self.host_path.name)
#
#     @guest_only
#     def build_source_package(self) -> str:
#         """
#         Build a source package in /srv/moncic-ci/source returning the name of
#         the main file of the source package fileset
#         """
#         return self.guest_path


class DebianDir(DebianSource, Dir):
    """
    Directory with Debian sources, without git
    """

    NAME = "debian-dir"

    source_info: SourceInfo
    tarball: Path

    def __init__(self, *, source_info: SourceInfo, tarball: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.source_info = source_info
        self.tarball = tarball


#     @host_only
#     def gather_sources_from_host(self, build: Build, container: Container) -> None:
#         """
#         Gather needed source files from the host system and copy them to the
#         guest
#         """
#         super().gather_sources_from_host(build, container)
#
#         tarball_search_dirs = [os.path.dirname(self.source.path)]
#         self._find_tarball(build, container, tarball_search_dirs)
#
#     @guest_only
#     def build_source_package(self) -> str:
#         """
#         Build a source package in /srv/moncic-ci/source returning the name of
#         the main file of the source package fileset
#         """
#         # Uses --no-pre-clean to avoid requiring build-deps to be installed at
#         # this stage
#         run(["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"], cwd=self.guest_path)
#
#         for fn in os.listdir("/srv/moncic-ci/source"):
#             if fn.endswith(".dsc"):
#                 return os.path.join("/srv/moncic-ci/source", fn)
#
#         raise RuntimeError(".dsc file not found after dpkg-buildpackage -S")


class DebianGitLegacy(DebianDir, Git):
    """
    Debian sources from a git repository, without gbp-buildpackage
    """


# class DebianGit(DebianDirMixin, DebianGitSource):
#     """
#     Debian git working directory that does not use git-buildpackage.
#
#     This is autoselected if the `debian/` directory exists, but there is no
#     `debian/gbp.conf`.
#
#     An upstream `orig.tar.gz` tarball is searched on `..` and on the artifacts
#     directory, and used if found.
#
#     If no existing upstream tarball is found, one is generated using
#     `git archive HEAD . ":(exclude)debian"`, as a last-resort measure.
#     """
#
#     NAME = "debian-git-plain"
#
#     @classmethod
#     def _create_from_repo(cls, distro: Distro, source: LocalGit, debversion: str) -> DebianPlainGit:
#         if not source.copy:
#             log.info(
#                 "%s: cloning repository to avoid building a potentially dirty working directory",
#                 source.repo.working_dir,
#             )
#             source = source.clone()
#
#         if source.repo.working_dir is None:
#             raise RuntimeError(f"{source} repository has no working directory")
#
#         res = cls(source, Path(source.repo.working_dir), debversion=debversion)
#         res.add_trace_log("git", "clone", "-b", source.repo.active_branch.name, source.source)
#         return res
#
#     @host_only
#     def gather_sources_from_host(self, build: Build, container: Container) -> None:
#         """
#         Gather needed source files from the host system and copy them to the
#         guest
#         """
#         super().gather_sources_from_host(build, container)
#
#         tarball_search_dirs = []
#         if self.source.orig_path is not None:
#             tarball_search_dirs.append(os.path.dirname(self.source.orig_path))
#         self._find_tarball(build, container, tarball_search_dirs)
#         if self.tarball_source is None:
#             self.build_orig_tarball(container)
#
#     @host_only
#     def build_orig_tarball(self, container: Container):
#         """
#         Make sure srcinfo.tar_fname exists.
#
#         This function is run from a clean source directory
#         """
#         if self.tarball_filename is None:
#             raise RuntimeError("tarball file not found")
#         source_dir = os.path.join(container.get_root(), "srv", "moncic-ci", "source")
#         source_stat = os.stat(source_dir)
#         dest_tarball = os.path.join(source_dir, self.tarball_filename)
#         with lzma.open(dest_tarball, "wb") as out:
#             with context.moncic.get().privs.user():
#                 # This is a last-resort measure, trying to build an approximation of an
#                 # upstream tarball when none was found
#                 log.info("Building tarball from source directory")
#                 cmd = ["git", "archive", "HEAD", ".", ":(exclude)debian"]
#                 log_run(cmd, cwd=self.source.path)
#                 proc = subprocess.Popen(cmd, cwd=self.source.path, stdout=subprocess.PIPE)
#                 shutil.copyfileobj(proc.stdout, out)
#                 if proc.wait() != 0:
#                     raise RuntimeError(f"git archive exited with error code {proc.returncode}")
#
#                 self.tarball_source = "[git archive HEAD . :(exclude)debian]"
#         os.chown(dest_tarball, source_stat.st_uid, source_stat.st_gid)
#
#     @guest_only
#     def build_source_package(self) -> str:
#         """
#         Build a source package in /srv/moncic-ci/source returning the name of
#         the main file of the source package fileset
#         """
#         # Uses --no-pre-clean to avoid requiring build-deps to be installed at
#         # this stage
#         run(["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"], cwd=self.guest_path)
#
#         for fn in os.listdir("/srv/moncic-ci/source"):
#             if fn.endswith(".dsc"):
#                 return os.path.join("/srv/moncic-ci/source", fn)
#
#         raise RuntimeError(".dsc file not found after dpkg-buildpackage -S")


class DebianGBP(DebianSource, Git, abc.ABC):
    """
    Debian git working directory with a gbp-buildpackage setup
    """


#    upstream_tag: str = ""
#    upstream_branch: str = ""
#    debian_tag: str = ""
#    gbp_args: list[str] = field(default_factory=list)
#
#    @classmethod
#    def parse_gbp(cls, gbp_conf_path: Path, debversion: str) -> dict[str, str]:
#        """
#        Parse gbp.conf returning values for DebianGBP fields
#        """
#        res: dict[str, str] = {"debversion": debversion}
#
#        # Parse gbp.conf
#        cfg = ConfigParser()
#        cfg.read(gbp_conf_path)
#        res["upstream_branch"] = cfg.get("DEFAULT", "upstream-branch", fallback="upstream")
#        upstream_tag = cfg.get("DEFAULT", "upstream-branch", fallback="upstream/%(version)s")
#        debian_tag = cfg.get("DEFAULT", "debian-tag", fallback="debian/%(version)s")
#
#        if "-" in debversion:
#            uv, dv = debversion.split("-", 1)
#            res["upstream_tag"] = upstream_tag % {"version": uv}
#            res["debian_tag"] = debian_tag % {"version": debversion}
#
#        return res
#
#    @classmethod
#    def read_upstream_branch(cls, repo: git.Repo) -> str | None:
#        """
#        Read the upstream branch from gbp.conf
#
#        Return None if gbp.conf does not exists or it does not specify an upstream branch
#        """
#        cfg = ConfigParser()
#        cfg.read([os.path.join(repo.working_dir, "debian", "gbp.conf")])
#        return cfg.get("DEFAULT", "upstream-branch", fallback=None)
#
#    @classmethod
#    def read_upstream_tag(cls, repo: git.Repo) -> str | None:
#        """
#        Read the upstream tag from gbp.conf
#
#        Return the default value if gbp.conf does not exists or it does not specify an upstream tag
#        """
#        cfg = ConfigParser()
#        cfg.read([os.path.join(repo.working_dir, "debian", "gbp.conf")])
#        return cfg.get("DEFAULT", "upstream-tag", fallback="upstream/%(version)s")
#
#    @guest_only
#    def build_source_package(self) -> str:
#        """
#        Build a source package in /srv/moncic-ci/source returning the name of
#        the main file of the source package fileset
#        """
#        cmd = ["gbp", "buildpackage", "--git-ignore-new", "-d", "-S", "--no-sign", "--no-pre-clean"]
#        cmd += self.gbp_args
#        run(cmd, cwd=self.guest_path)
#
#        for fn in os.listdir("/srv/moncic-ci/source"):
#            if fn.endswith(".dsc"):
#                return os.path.join("/srv/moncic-ci/source", fn)
#
#        raise RuntimeError(".dsc file not found after gbp buildpackage -S")


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


#    @classmethod
#    def _create_from_repo(cls, distro: Distro, source: LocalGit) -> DebianGBPTestUpstream:
#        # find the right debian branch
#        candidate_branches = distro.get_gbp_branches()
#        for branch in candidate_branches:
#            if source.find_branch(branch) is not None:
#                break
#        else:
#            raise Fail(
#                f"Packaging branch not found for distribution '{distro}'." f" Tried: {', '.join(candidate_branches)} "
#            )
#
#        # TODO: find common ancestor between current and packaging, and merge
#        #       packaging branch from that?
#
#        # If we are still working on an uncloned repository, create a temporary
#        # clone to avoid mangling it
#        if not source.copy:
#            log.info("%s: cloning repository to avoid mangling the original version", source.repo.working_dir)
#            source = source.clone()
#
#        trace_log: list[Sequence[str, ...]] = []
#
#        # Make a temporary merge of active_branch on the debian branch
#        log.info("merge packaging branch %s for test build", branch)
#        active_branch = source.repo.active_branch.name
#        if active_branch is None:
#            log.info("repository is in detached head state, creating a 'moncic-ci' working branch from it")
#            trace_log.append(("git", "clone", source.source))
#            cmd = ["git", "checkout", source.repo.head.commit.hexsha, "-b", "moncic-ci"]
#            trace_log.append(cmd)
#            run(cmd, cwd=source.repo.working_dir)
#            active_branch = "moncic-ci"
#        else:
#            trace_log.append(("git", "clone", "-b", active_branch, source.source))
#
#        cmd = ["git", "checkout", "--quiet", branch]
#        trace_log.append(cmd)
#        run(cmd, cwd=source.repo.working_dir)
#
#        cmd = [
#            "git",
#            "-c",
#            "user.email=moncic-ci@example.org",
#            "-c",
#            "user.name=Moncic-CI",
#            "merge",
#            "--quiet",
#            str(active_branch),
#            "-m",
#            "CI merge",
#        ]
#        trace_log.append(cmd)
#        run(cmd, cwd=source.repo.working_dir)
#
#        path = Path(source.repo.working_dir)
#        debversion = cls.read_changelog_version(path / "debian" / "changelog")
#        gbp_args = cls.parse_gbp(path / "debian" / "gbp.conf", debversion)
#
#        res = cls(source, path, **gbp_args)
#        for args in trace_log:
#            res.add_trace_log(*args)
#
#        res.gbp_args.append("--git-upstream-tree=branch")
#        res.gbp_args.append("--git-upstream-branch=" + active_branch)
#        return res


class DebianGBPRelease(DebianGBP):
    """
    Debian git working directory checked out to a tagged release branch.

    This is autoselected if the git commit being built is a git tag, and it
    contains a `debian/` directory.

    `git-buildpackage` is invoked with `--git-upstream-tree=tag`, to build the
    release version of a package.
    """

    NAME = "debian-gbp-release"


#    @classmethod
#    def _create_from_repo(cls, distro: Distro, source: LocalGit, debversion: str) -> DebianGBPRelease:
#        # TODO: check that debian/changelog is not UNRELEASED
#        # The current directory is already the right source directory
#
#        # If we are still working on an uncloned repository, create a temporary
#        # clone to work on a clean one
#        if not source.copy:
#            log.info("%s: cloning repository to avoid mangling the original version", source.repo.working_dir)
#            source = source.clone()
#
#        path = Path(source.repo.working_dir)
#
#        gbp_args = cls.parse_gbp(path / "debian" / "gbp.conf", debversion)
#
#        res = cls(source, path, **gbp_args)
#        res.gbp_args.append("--git-upstream-tree=tag")
#        return res
#
#    def _check_debian_commits(self, linter: lint.Linter):
#        repo = self.source.repo
#
#        # Check files modified, ensure it's only in debian/
#        upstream = repo.commit(self.upstream_tag)
#        debian = repo.commit(self.debian_tag)
#        upstream_affected: set[str] = set()
#        for diff in upstream.diff(debian):
#            if diff.a_path is not None and not diff.a_path.startswith("debian/"):
#                upstream_affected.add(diff.a_path)
#            if diff.b_path is not None and not diff.b_path.startswith("debian/"):
#                upstream_affected.add(diff.b_path)
#
#        for name in sorted(upstream_affected):
#            linter.warning(f"{name}: upstream file affected by debian branch")
#
#    def lint(self, linter: lint.Linter):
#        super().lint(linter)
#        self._check_debian_commits(linter)


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


#    @classmethod
#    def _create_from_repo(cls, distro: Distro, source: LocalGit, debversion: str) -> DebianGBPTestDebian:
#        # Read the upstream branch to use from gbp.conf
#        upstream_branch = cls.read_upstream_branch(source.repo)
#        if upstream_branch is None:
#            raise RuntimeError("Cannot read upstream branch from debian/gbp.conf")
#
#        # If we are still working on an uncloned repository, create a temporary
#        # clone to avoid mangling it
#        if not source.copy:
#            log.info("%s: cloning repository to avoid mangling the original version", source.repo.working_dir)
#            source = source.clone()
#
#        path = Path(source.repo.working_dir)
#
#        gbp_args = cls.parse_gbp(path / "debian" / "gbp.conf", debversion)
#
#        res = cls(source, path, **gbp_args)
#        res.add_trace_log("git", "clone", "-b", str(source.repo.active_branch), source.source)
#
#        # Merge the upstream branch into the debian branch
#        log.info("merge upstream branch %s into build branch", upstream_branch)
#        cmd = [
#            "git",
#            "-c",
#            "user.email=moncic-ci@example.org",
#            "-c",
#            "user.name=Moncic-CI",
#            "merge",
#            upstream_branch,
#            "--quiet",
#            "-m",
#            "CI merge",
#        ]
#        res.add_trace_log(*cmd)
#        run(cmd, cwd=source.repo.working_dir)
#
#        res.gbp_args.append("--git-upstream-tree=branch")
#        return res
#
#    def _check_debian_commits(self, linter: lint.Linter):
#        repo = self.source.repo
#
#        # Check files modified, ensure it's only in debian/
#        upstream = repo.commit(self.upstream_tag)
#        debian = repo.head.commit
#        upstream_affected: set[str] = set()
#        for diff in upstream.diff(debian):
#            if diff.a_path is not None and not diff.a_path.startswith("debian/"):
#                upstream_affected.add(diff.a_path)
#            if diff.b_path is not None and not diff.b_path.startswith("debian/"):
#                upstream_affected.add(diff.b_path)
#
#        for name in sorted(upstream_affected):
#            linter.warning(f"{name}: upstream file affected by debian branch")
#
#    def lint(self, linter: lint.Linter):
#        super().lint(linter)
#        self._check_debian_commits(linter)
