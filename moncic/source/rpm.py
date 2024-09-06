from __future__ import annotations

import abc
import itertools
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

from .. import lint
from ..container import ContainerConfig
from ..exceptions import Fail
from .local import Dir, Git, File
from .source import Source
from .distro import DistroSource

if TYPE_CHECKING:
    from ..build import Build
    from ..container import System
    from ..distro import Distro

log = logging.getLogger(__name__)


class RPMSource(DistroSource, abc.ABC):
    """
    RPM source
    """

    #: Path to the specfile to use for build
    specfile_path: Path

    def __init__(self, specfile_path: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.specfile_path = specfile_path

    @classmethod
    def create_from_file(cls, parent: File, *, distro: Distro, style: str | None = None) -> NoReturn:
        if parent.path.suffix == ".dsc":
            raise Fail(f"{parent.path}: cannot build Debian source package on a RPM distribution")
        else:
            raise Fail(f"{parent.path}: cannot detect source type")

    @classmethod
    def create_from_dir(cls, parent: Dir, *, distro: Distro, style: str | None = None) -> "ARPASourceDir":
        specfile_paths = ARPASourceDir.locate_specfiles(parent.path)
        if not specfile_paths:
            raise Fail(f"{parent.path}: no specfiles found in well-known locations")
        if len(specfile_paths) > 1:
            raise Fail(f"{parent.path}: {len(specfile_paths)} specfiles found")
        return ARPASourceDir(parent=parent, path=parent.path, specfile_path=specfile_paths[0], distro=distro)

    @classmethod
    def create_from_git(cls, parent: Git, *, distro: Distro, style: str | None = None) -> "ARPASourceGit":
        specfile_paths = ARPASourceGit.locate_specfiles(parent.path)
        if not specfile_paths:
            raise Fail(f"{parent.path}: no specfiles found in well-known locations")
        if len(specfile_paths) > 1:
            raise Fail(f"{parent.path}: {len(specfile_paths)} specfiles found")

        # FIXME: cast not needed after python 3.11
        return cast(
            ARPASourceGit, ARPASourceGit.derive_from_git(parent, specfile_path=specfile_paths[0], distro=distro)
        )


#    @classmethod
#    def detect(cls, distro: Distro, source: LocalGit | LocalDir) -> RPMSource:
#        """
#        Auto detect the style of RPM source to build.
#
#        RPM source package layouts are not really standardized, as the specfile
#        is generally assumed to be outside the git repository.
#
#        This is not the case in ARPA (https://www.arpae.it/), so here we can
#        delegate to source styles implementing ARPA's local rules.
#
#        Should more RPM source styles emerge/standardize to contain a specfile
#        in the git repository, this is the place where support for them can be
#        added.
#        """
#        if isinstance(source, LocalGit):
#            return ARPAGitSource._create_from_repo(source)
#        elif isinstance(source, LocalDir):
#            return ARPASource._create_from_repo(source)
#        else:
#            raise RuntimeError(
#                f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}"
#            )


class ARPASource(RPMSource, abc.ABC):
    """
    Base class for ARPA sources.

    This source is expected to follow the standard used for RPM packaging by
    ARPAE-SIMC (https://www.arpae.it)
    """

    #    def get_build_class(self) -> type[Build]:
    #        from ..build.arpa import ARPA
    #
    #        return ARPA
    #
    #    def get_linter_class(self) -> type[lint.Linter]:
    #        return lint.ARPALinter

    @classmethod
    def locate_specfiles(cls, path: Path) -> list[Path]:
        """
        Locate the specfile inside the given directory.

        Return its path relative to the given path
        """
        spec_globs = ["fedora/SPECS/*.spec", "*.spec"]
        return [p.relative_to(path) for p in itertools.chain.from_iterable(path.glob(g) for g in spec_globs)]


#    def find_versions(self, system: System) -> dict[str, str]:
#        versions = super().find_versions(system)
#
#        spec_path = self.specfile_path
#
#        # Run in container: rpmspec --parse file.spec
#        if (self.host_path / spec_path).exists():
#            cconfig = ContainerConfig()
#            cconfig.configure_workdir(self.host_path, bind_type="ro")
#            with system.create_container(config=cconfig) as container:
#                res = container.run(["/usr/bin/rpmspec", "--parse", spec_path])
#            if res.returncode == 0:
#                version: str | None = None
#                release: str | None = None
#                for line in res.stdout.splitlines():
#                    if line.startswith(b"Version:"):
#                        if version is None:
#                            version = line[8:].strip().decode()
#                    if line.startswith(b"Release:"):
#                        if release is None:
#                            release = line[8:].strip().decode()
#
#                if version is not None:
#                    versions["spec-upstream"] = version
#                    if release is not None:
#                        versions["spec-release"] = version + "-" + release
#
#        return versions


class ARPASourceDir(ARPASource, Dir):
    """
    ARPA/SIMC source directory, building RPM packages using the logic
    previously configured for travis
    """

    NAME = "rpm-arpa"


#    @classmethod
#    def _create_from_repo(cls, source: LocalDir) -> ARPASource:
#        return cls(source, Path(source.path))
#
#    @classmethod
#    def create(cls, distro: Distro, source: InputSource) -> ARPASource:
#        if isinstance(source, LocalGit):
#            raise Fail(
#                f"Cannot use {cls.NAME} source type on a {type(source).__name__} source:"
#                f" maybe try {ARPAGitSource.NAME}?"
#            )
#        if not isinstance(source, LocalDir):
#            raise Fail(f"Cannot use {cls.NAME} source type on a {type(source).__name__} source")
#        return cls._create_from_repo(source)


class ARPASourceGit(ARPASource, Git):
    """
    ARPA/SIMC git repository, building RPM packages using the logic previously
    configured for travis
    """

    NAME = "rpm-arpa-git"


#    @classmethod
#    def _create_from_repo(cls, source: LocalGit) -> ARPAGitSource:
#        return cls(source, Path(source.path))
#
#    @classmethod
#    def create(cls, distro: Distro, source: InputSource) -> ARPAGitSource:
#        if isinstance(source, LocalDir):
#            raise Fail(
#                f"Cannot use {cls.NAME} source type on a {type(source).__name__} source: maybe try {ARPASource.NAME}?"
#            )
#        if not isinstance(source, LocalGit):
#            raise Fail(f"Cannot use {cls.NAME} source type on a {type(source).__name__} source")
#        return cls._create_from_repo(source)
#
#    def _check_arpa_commits(self, linter: lint.Linter):
#        repo = self.source.repo
#
#        # Get the latest version tag
#        res = subprocess.run(
#            ["git", "describe", "--tags", "--abbrev=0", "--match=v[0-9]*"],
#            cwd=repo.working_dir,
#            text=True,
#            capture_output=True,
#            check=True,
#        )
#        last_tag = res.stdout.strip()
#
#        if "-" not in last_tag:
#            return
#
#        # Look for a previous version tag
#        uv, rv = last_tag[1:].split("-", 1)
#        if (last_ver := int(rv)) == 1:
#            return
#
#        prev_ver: int | None = None
#        prev_tag: str | None = None
#        prefix = f"v{uv}-"
#        for tag in repo.tags:
#            if tag.name.startswith(prefix):
#                ver = int(tag.name[len(prefix) :])
#                if ver < last_ver:
#                    if prev_ver is None or prev_ver < ver:
#                        prev_ver = ver
#                        prev_tag = tag.name
#
#        if prev_ver is None:
#            linter.warning(f"Found tag {last_tag} but no earlier release tag for the same upstream version")
#            return
#
#        # Check that the diff between the two tags only affects files under the
#        # same directory as the specfile
#        changes_root = os.path.dirname(self.specfile_path)
#        prev = repo.commit(prev_tag)
#        last = repo.commit(last_tag)
#        upstream_affected: set[str] = set()
#        for diff in prev.diff(last):
#            if diff.a_path is not None and not diff.a_path.startswith(changes_root):
#                upstream_affected.add(diff.a_path)
#            if diff.b_path is not None and not diff.b_path.startswith(changes_root):
#                upstream_affected.add(diff.b_path)
#
#        for name in sorted(upstream_affected):
#            linter.warning(f"{name}: upstream file affected by packaging changes")
#
#    def lint(self, linter: lint.Linter):
#        super().lint(linter)
#        self._check_arpa_commits(linter)
