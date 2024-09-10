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
from typing import TYPE_CHECKING, NamedTuple, cast, Any

import git

from .. import context, lint
from ..build.utils import link_or_copy
from ..exceptions import Fail
from ..utils.guest import guest_only, host_only
from ..utils.run import log_run, run
from .local import Dir, Git, File
from .source import Source, CommandLog
from .distro import DistroSource
from ..distro.debian import DebianDistro

if TYPE_CHECKING:
    from ..build import Build
    from ..container import Container, System
    from ..distro import Distro

log = logging.getLogger(__name__)

re_debchangelog_head = re.compile(r"^(?P<name>\S+) \((?:[^:]+:)?(?P<version>[^)]+)\)")


@dataclass(kw_only=True)
class SourceInfo:
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
    def create_from_dir(cls, path: Path) -> "SourceInfo":
        """
        Get source information from an unpacked Debian source
        """
        with (path / "debian" / "changelog").open() as fd:
            if mo := re_debchangelog_head.match(next(fd)):
                name = mo.group("name")
                version = mo.group("version")
            else:
                raise Fail("Unparsable debian/changelog")

        return cls(**cls._infer_args_from_name_version(name, version))

    @classmethod
    def _infer_args_from_name_version(cls, name: str, version: str, **kwargs) -> dict[str, str]:
        res: dict[str, str] = {
            "name": name,
            "version": version,
        }
        version_dsc = version.split(":", 1)[1] if ":" in version else version
        if "-" in version_dsc:
            upstream_version = version_dsc.split("-", 1)[0]
            res["tar_stem"] = f"{name}_{upstream_version}.orig.tar"
        else:
            res["tar_stem"] = f"{name}_{version_dsc}.tar"
        res["dsc_filename"] = f"{name}_{version_dsc}.dsc"
        res.update(kwargs)
        return res

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

    def parse_gbp(self, gbp_conf_path: Path) -> "GBPInfo":
        """
        Parse gbp.conf returning values for DebianGBP fields
        """
        # Parse gbp.conf
        cfg = ConfigParser()
        cfg.read(gbp_conf_path)
        upstream_branch = cfg.get("DEFAULT", "upstream-branch", fallback="upstream")
        upstream_tag = cfg.get("DEFAULT", "upstream-branch", fallback="upstream/%(version)s")
        debian_tag = cfg.get("DEFAULT", "debian-tag", fallback="debian/%(version)s")

        if "-" in self.version:
            uv, dv = self.version.split("-", 1)
            upstream_tag = upstream_tag % {"version": uv}
            debian_tag = debian_tag % {"version": self.version}

        return GBPInfo(upstream_branch=upstream_branch, upstream_tag=upstream_tag, debian_tag=debian_tag)


@dataclass(kw_only=True)
class DSCInfo(SourceInfo):
    """Information read from a .dsc file"""

    file_list: list[str]

    @classmethod
    def create_from_file(cls, path: Path) -> "DSCInfo":
        name: str | None = None
        version: str | None = None
        file_list: list[str] = []

        re_file = re.compile(r"^\s+\S+\s+\d+\s+(\S+)\s*$")

        with path.open() as fd:
            files_section = False
            for line in fd:
                if not files_section:
                    if line.startswith("Source: "):
                        name = line[8:].strip()
                    elif line.startswith("Version: "):
                        version = line[9:].strip()
                    elif line.startswith("Files:"):
                        files_section = True
                else:
                    if mo := re_file.match(line):
                        file_list.append(mo.group(1))
                    else:
                        files_section = False

        if name is None:
            raise Fail(f"{path}: Source: entry not found")
        if version is None:
            raise Fail(f"{path}: Version: entry not found")

        return cls(**cls._infer_args_from_name_version(name, version, file_list=file_list))


@dataclass(kw_only=True)
class GBPInfo:
    """
    Information from a gbp.conf file
    """

    upstream_branch: str
    upstream_tag: str
    debian_tag: str


class DebianSource(DistroSource, abc.ABC):
    """
    Base class for Debian source packages
    """

    source_info: SourceInfo

    def __init__(self, *, source_info: SourceInfo, **kwargs) -> None:
        super().__init__(**kwargs)
        self.source_info = source_info

    def add_init_args_for_derivation(self, kwargs: dict[str, Any]) -> None:
        super().add_init_args_for_derivation(kwargs)
        kwargs["source_info"] = self.source_info

    def build_source_package(self) -> Path:
        """
        Build a source package and return the .dsc file name.

        :param path: the source file or directory in the guest system
        :return: path to the resulting .dsc file
        """
        raise NotImplementedError(f"{self.__class__.__name__}.build_source_package not implemented")

    @classmethod
    def create_from_file(cls, parent: File, *, distro: Distro) -> "DebianSource":
        if not isinstance(distro, DebianDistro):
            raise RuntimeError("cannot create a DebianSource non a non-Debian distro")
        if parent.path.suffix == ".dsc":
            return DebianDsc.prepare_from_file(parent, distro=distro)
        else:
            raise Fail(f"{parent.path}: cannot detect source type")

    @classmethod
    def create_from_dir(cls, parent: Dir, *, distro: Distro) -> "DebianSource":
        if not (parent.path / "debian").is_dir():
            raise Fail(f"{parent.path}: cannot detect source type")
        if not isinstance(distro, DebianDistro):
            raise RuntimeError("cannot create a DebianSource non a non-Debian distro")

        return DebianDir.prepare_from_dir(parent, distro=distro)

    @classmethod
    def create_from_git(cls, parent: Git, *, distro: Distro) -> "DebianSource":
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
        repo = parent.repo
        if repo.working_dir is None:
            raise RuntimeError(f"{parent.path} has no working directory")

        if not isinstance(distro, DebianDistro):
            raise RuntimeError("cannot create a DebianSource non a non-Debian distro")

        debian_path = parent.path / "debian"
        if not debian_path.exists() or not (debian_path / "changelog").exists():
            log.debug("%s: debian/ directory not found: looking for a packaging branch", parent)
            # There is no debian/changelog: the current branch could be
            # upstream in a gbp repository
            packaging_branch = DebianGBP.find_packaging_branch(parent, distro)
            if packaging_branch is None:
                raise Fail(f"{parent.path}: cannot detect source type")
            log.debug("%s: found a packaging branch, using DebianGBPTestUpstream", parent)
            return DebianGBPTestUpstream.prepare_from_git(parent, distro=distro, packaging_branch=packaging_branch)

        log.debug("%s: found debian/ directory", parent)
        source_info = SourceInfo.create_from_dir(parent.path)

        # Check if it's a gbp-buildpackage source
        gbp_conf_path = debian_path / "gbp.conf"
        if not gbp_conf_path.exists():
            log.debug("%s: gbp.conf not found, using DebianGitLegacy", parent)
            return DebianGitLegacy.prepare_from_git(parent, distro=distro, source_info=source_info)

        gbp_info = source_info.parse_gbp(parent.path / "debian" / "gbp.conf")
        log.debug(
            "%s: gbp.conf found: upstream_branch=%s, upstream_tag=%s, debian_tag=%s",
            parent,
            gbp_info.upstream_branch,
            gbp_info.upstream_tag,
            gbp_info.debian_tag,
        )

        # Check if we are building a tagged commit
        if parent.find_tags():
            # If branch to build is a tag, build a release from it
            log.debug("%s: branch is tagged, using DebianGBPRelease", parent)
            return DebianGBPRelease.prepare_from_git(parent, distro=distro, source_info=source_info, gbp_info=gbp_info)

        # There is a debian/ directory, find upstream from gbp.conf
        log.debug("%s: branch is not tagged, using DebianGBPTestDebian", parent)
        return DebianGBPTestDebian.prepare_from_git(parent, distro=distro, source_info=source_info, gbp_info=gbp_info)

    def _find_built_dsc(self) -> Path:
        # Try with the expected .dsc name
        dsc_path = self.path.parent / self.source_info.dsc_filename
        if dsc_path.exists():
            return dsc_path

        # Something unexpected happened: look harder for a built .dsc file
        for sub in self.path.parent.iterdir():
            if sub.suffix == ".dsc":
                log.warning("found .dsc file %s instead of %s", sub, dsc_path)
                return sub

        raise RuntimeError(".dsc file not found after dpkg-buildpackage -S")


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

    source_info: DSCInfo

    def __init__(self, *, source_info: DSCInfo, **kwargs) -> None:
        super().__init__(source_info=source_info, **kwargs)

    @classmethod
    def prepare_from_file(cls, parent: File, *, distro: DebianDistro) -> "DebianDsc":
        assert parent.path.suffix == ".dsc"
        source_info = DSCInfo.create_from_file(parent.path)
        return cls(parent=parent, path=parent.path, distro=distro, source_info=source_info)

    @host_only
    def collect_build_artifacts(self, destdir: Path, artifact_dir: Path | None = None) -> None:
        super().collect_build_artifacts(destdir, artifact_dir)
        # Copy .dsc and its assets to the container
        srcdir = self.path.parent
        file_list = [self.path.name]
        file_list += self.source_info.file_list
        for fname in file_list:
            link_or_copy(srcdir / fname, destdir)

    def build_source_package(self) -> Path:
        return self.path


class DebianDir(DebianSource, Dir):
    """
    Directory with Debian sources, without git
    """

    NAME = "debian-dir"

    @classmethod
    def prepare_from_dir(
        cls,
        parent: Dir,
        *,
        distro: DebianDistro,
    ) -> "DebianDir":  # TODO: Self from python 3.11+
        source_info = SourceInfo.create_from_dir(parent.path)
        return cls(**parent.derive_kwargs(distro=distro, source_info=source_info))

    def _find_tarball(self, artifact_dir: Path | None = None) -> Path | None:
        search_path = []
        if artifact_dir:
            search_path.append(artifact_dir)
        search_path.append(self.path.parent)
        return self.source_info.find_tarball(search_path)

    def _on_tarball_not_found(self, destdir: Path) -> None:
        """
        Hook called if the tarball was not found, to allow a subclass to generate it
        """
        raise Fail(f"Tarball {self.source_info.tar_stem}.* not found")

    @host_only
    def collect_build_artifacts(self, destdir: Path, artifact_dir: Path | None = None) -> None:
        super().collect_build_artifacts(destdir, artifact_dir)
        tarball = self._find_tarball(artifact_dir)
        if tarball is None:
            self._on_tarball_not_found(destdir)
        else:
            link_or_copy(tarball, destdir)

    def build_source_package(self) -> Path:
        # Uses --no-pre-clean to avoid requiring build-deps to be installed at
        # this stage
        run(["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"], cwd=self.path)

        return self._find_built_dsc()


class DebianGitLegacy(DebianDir, Git):
    """
    Debian sources from a git repository, without gbp-buildpackage
    """

    @classmethod
    def prepare_from_git(
        cls,
        parent: Git,
        *,
        distro: DebianDistro,
        source_info: SourceInfo,
    ) -> "DebianGitLegacy":  # TODO: Self from python 3.11+
        parent = parent.get_clean()
        return cls(**parent.derive_kwargs(distro=distro, source_info=source_info))

    def _on_tarball_not_found(self, destdir: Path) -> None:
        """
        Hook called if the tarball was not found, to allow a subclass to generate it
        """
        source_stat = self.path.stat()
        dest_tarball = destdir / (self.source_info.tar_stem + ".xz")
        with lzma.open(dest_tarball, "wb") as out:
            # This is a last-resort measure, trying to build an approximation of an
            # upstream tarball when none was found
            log.info("%s: building tarball from source directory", self)
            cmd = ["git", "archive", "HEAD", ".", ":(exclude)debian"]
            log_run(cmd, cwd=self.path)
            proc = subprocess.Popen(cmd, cwd=self.path, stdout=subprocess.PIPE)
            assert proc.stdout
            shutil.copyfileobj(proc.stdout, out)
            if proc.wait() != 0:
                raise RuntimeError(f"git archive exited with error code {proc.returncode}")
        os.chown(dest_tarball, source_stat.st_uid, source_stat.st_gid)


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

    gbp_info: GBPInfo
    gbp_args: list[str]

    def __init__(self, *, gbp_info: GBPInfo, gbp_args: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.gbp_info = gbp_info
        self.gbp_args = gbp_args

    def add_init_args_for_derivation(self, kwargs: dict[str, Any]) -> None:
        super().add_init_args_for_derivation(kwargs)
        kwargs["gbp_info"] = self.gbp_info
        kwargs["gbp_args"] = self.gbp_args

    @classmethod
    def find_packaging_branch(cls, source: Git, distro: DebianDistro) -> git.refs.symbolic.SymbolicReference | None:
        """
        Find the Debian packaging branch for the given distro.

        :return: the ref of the branch if found, else None
        """
        candidate_branches = distro.get_gbp_branches()
        for branch in candidate_branches:
            if (ref := source.find_branch(branch)) is not None:
                return ref
        return None

    def build_source_package(self) -> Path:
        """
        Build a source package in /srv/moncic-ci/source returning the name of
        the main file of the source package fileset
        """
        cmd = ["gbp", "buildpackage", "--git-ignore-new", "-d", "-S", "--no-sign", "--no-pre-clean"]
        cmd += self.gbp_args
        run(cmd, cwd=self.path)

        return self._find_built_dsc()


#     @classmethod
#     def read_upstream_branch(cls, repo: git.Repo) -> str | None:
#         """
#         Read the upstream branch from gbp.conf
#
#         Return None if gbp.conf does not exists or it does not specify an upstream branch
#         """
#         cfg = ConfigParser()
#         cfg.read([os.path.join(repo.working_dir, "debian", "gbp.conf")])
#         return cfg.get("DEFAULT", "upstream-branch", fallback=None)
#
#     @classmethod
#     def read_upstream_tag(cls, repo: git.Repo) -> str | None:
#         """
#         Read the upstream tag from gbp.conf
#
#         Return the default value if gbp.conf does not exists or it does not specify an upstream tag
#         """
#         cfg = ConfigParser()
#         cfg.read([os.path.join(repo.working_dir, "debian", "gbp.conf")])
#         return cfg.get("DEFAULT", "upstream-tag", fallback="upstream/%(version)s")


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
    def prepare_from_git(
        cls,
        parent: Git,
        *,
        distro: DebianDistro,
        packaging_branch: git.refs.symbolic.SymbolicReference,
    ) -> "DebianGBPTestUpstream":
        # TODO: find common ancestor between current and packaging, and merge
        #       packaging branch from that?

        # If we are still working on an uncloned repository, create a temporary
        # clone to avoid mangling it
        parent = parent.get_writable()
        branch = packaging_branch.name

        command_log = CommandLog()

        # Make a temporary merge of active_branch on the debian branch
        log.info("merge packaging branch %s for test build", branch)
        active_branch = parent.repo.active_branch.name
        if active_branch is None:
            log.info("repository is in detached head state, creating a 'moncic-ci' working branch from it")
            cmd = ["git", "checkout", parent.repo.head.commit.hexsha, "-b", "moncic-ci"]
            command_log.run(cmd, cwd=parent.repo.working_dir)
            active_branch = "moncic-ci"

        command_log.run(["git", "checkout", "--quiet", branch], cwd=parent.repo.working_dir)

        command_log.run(
            [
                "git",
                "-c",
                "user.email=moncic-ci@example.org",
                "-c",
                "user.name=Moncic-CI",
                "merge",
                "--quiet",
                active_branch,
                "-m",
                "CI merge",
            ],
            cwd=parent.repo.working_dir,
        )

        source_info = SourceInfo.create_from_dir(parent.path)
        gbp_info = source_info.parse_gbp(parent.path / "debian" / "gbp.conf")

        return cls(
            **parent.derive_kwargs(
                distro=distro,
                source_info=source_info,
                gbp_info=gbp_info,
                command_log=command_log,
                gbp_args=["--git-upstream-tree=branch", f"--git-upstream-branch={active_branch}"],
            )
        )


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
    def prepare_from_git(
        cls,
        parent: Git,
        *,
        distro: DebianDistro,
        source_info: SourceInfo,
        gbp_info: GBPInfo,
    ) -> "DebianGBPRelease":
        # TODO: check that debian/changelog is not UNRELEASED
        # The current directory is already the right source directory

        return cls(
            **parent.derive_kwargs(
                distro=distro,
                source_info=source_info,
                gbp_info=gbp_info,
                gbp_args=["--git-upstream-tree=tag"],
            )
        )


#     def _check_debian_commits(self, linter: lint.Linter):
#         repo = self.source.repo
#
#         # Check files modified, ensure it's only in debian/
#         upstream = repo.commit(self.upstream_tag)
#         debian = repo.commit(self.debian_tag)
#         upstream_affected: set[str] = set()
#         for diff in upstream.diff(debian):
#             if diff.a_path is not None and not diff.a_path.startswith("debian/"):
#                 upstream_affected.add(diff.a_path)
#             if diff.b_path is not None and not diff.b_path.startswith("debian/"):
#                 upstream_affected.add(diff.b_path)
#
#         for name in sorted(upstream_affected):
#             linter.warning(f"{name}: upstream file affected by debian branch")


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

    @classmethod
    def prepare_from_git(
        cls,
        parent: Git,
        *,
        distro: DebianDistro,
        source_info: SourceInfo,
        gbp_info: GBPInfo,
    ) -> "DebianGBPTestDebian":
        # TODO: check that debian/changelog is not UNRELEASED
        # The current directory is already the right source directory

        # If we are still working on an uncloned repository, create a temporary
        # clone to work on a clean one
        parent = parent.get_writable()

        command_log = CommandLog()
        command_log.add_command("git", "clone", "-b", parent.repo.active_branch.name, parent.path.as_posix())

        # Merge the upstream branch into the debian branch
        log.info("merge upstream branch %s into build branch", gbp_info.upstream_branch)
        cmd = [
            "git",
            "-c",
            "user.email=moncic-ci@example.org",
            "-c",
            "user.name=Moncic-CI",
            "merge",
            gbp_info.upstream_branch,
            "--quiet",
            "-m",
            "CI merge",
        ]
        command_log.run(cmd, cwd=parent.path)

        # If we are still working on an uncloned repository, create a temporary
        # clone to work on a clean one
        parent = parent.get_writable()

        return cls(
            **parent.derive_kwargs(
                distro=distro,
                source_info=source_info,
                command_log=command_log,
                gbp_info=gbp_info,
                gbp_args=["--git-upstream-tree=branch"],
            )
        )


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
