from __future__ import annotations

import abc
import itertools
import logging
import shutil
import subprocess
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

import git

from ..distro.rpm import RpmDistro
from ..exceptions import Fail
from ..utils.run import run
from .distro import DistroSource
from .local import Dir, File, Git

if TYPE_CHECKING:
    from ..distro import Distro

log = logging.getLogger(__name__)


class RPMSource(DistroSource, abc.ABC):
    """
    RPM source
    """

    # RPM source package layouts are not really standardized, as the specfile
    # is generally assumed to be outside the git repository.
    #
    # This is not the case in ARPA (https://www.arpae.it/), so here we can
    # delegate to source styles implementing ARPA's local rules.
    #
    # Should more RPM source styles emerge/standardize to contain a specfile
    # in the git repository, this is the place where support for them can be
    # added.

    #: Path to the specfile to use for build
    specfile_path: Path

    def __init__(self, specfile_path: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.specfile_path = specfile_path

    def info_dict(self) -> dict[str, Any]:
        """Return JSON-able information about this source, without parent information."""
        res = super().info_dict()
        res["specfile_path"] = self.specfile_path.as_posix()
        return res

    def add_init_args_for_derivation(self, kwargs: dict[str, Any]) -> None:
        super().add_init_args_for_derivation(kwargs)
        kwargs["specfile_path"] = self.specfile_path

    @classmethod
    def create_from_file(cls, parent: File, *, distro: Distro) -> RPMSource:  # TODO: use Self from 3.11+
        if parent.path.suffix == ".dsc":
            raise Fail(f"{parent.path}: cannot build Debian source package on a RPM distribution")
        else:
            raise Fail(f"{parent.path}: cannot detect source type")

    @classmethod
    def create_from_dir(cls, parent: Dir, *, distro: Distro) -> RPMSource:  # TODO: use Self from 3.11+
        if not isinstance(distro, RpmDistro):
            raise RuntimeError("cannot create a RPMSource non a non-RPM distro")
        specfiles = ARPASourceDir.locate_specfiles(parent.path)
        return ARPASourceDir.prepare_from_dir(parent=parent, specfiles=specfiles, distro=distro)

    @classmethod
    def create_from_git(cls, parent: Git, *, distro: Distro) -> RPMSource:  # TODO: use Self from 3.11+
        if not isinstance(distro, RpmDistro):
            raise RuntimeError("cannot create a RPMSource non a non-RPM distro")
        specfiles = ARPASourceGit.locate_specfiles(parent.path)
        return ARPASourceGit.prepare_from_git(parent=parent, specfiles=specfiles, distro=distro)

    @cached_property
    def spec_versions(self) -> tuple[str | None, str | None]:
        """
        Return a tuple (upstream version, release version) as parsed from the
        specfile.

        Versions can be None if not found in the specfile
        """
        spec_path = self.path / self.specfile_path
        if not spec_path.exists():
            return None, None

        rpmspec = shutil.which("rpmspec")
        if rpmspec is None:
            log.warning("rpmspec not found, cannot parse specfile")
            return None, None

        res = run(
            [rpmspec, "--parse", spec_path.as_posix()],
            cwd=self.path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if res.returncode != 0:
            log.warning("%s: rpmspec failed to parse the specfile: %s", spec_path, res.stderr)
            return None, None

        version: str | None = None
        release: str | None = None
        for line in res.stdout.splitlines():
            if line.startswith("Version:"):
                if version is None:
                    version = line[8:].strip()
            if line.startswith("Release:"):
                if release is None:
                    release = line[8:].strip()

        if version is None:
            return None, None

        if release is not None:
            return version, version + "-" + release
        else:
            return version, None

    def lint_find_versions(self, *, allow_exec=False) -> dict[str, str]:
        versions = super().lint_find_versions(allow_exec=allow_exec)
        version, release = self.spec_versions
        if version is not None:
            versions["spec-upstream"] = version
        if release is not None:
            versions["spec-release"] = release
        return versions

    def lint_path_is_packaging(self, path: Path) -> bool:
        """
        Check if a path looks like packaging instead of upstream
        """
        return path.suffix == ".spec"


class ARPASource(RPMSource, abc.ABC, style="rpm-arpa"):
    """
    Base class for ARPA sources.

    This source is expected to follow the standard used for RPM packaging by
    ARPAE-SIMC (https://www.arpae.it)
    """

    @classmethod
    def locate_specfiles(cls, path: Path) -> list[Path]:
        """
        Locate the specfile inside the given directory.

        Return its path relative to the given path
        """
        spec_globs = ["fedora/SPECS/*.spec", "*.spec"]
        return [p.relative_to(path) for p in itertools.chain.from_iterable(path.glob(g) for g in spec_globs)]

    @classmethod
    def prepare_from_dir(
        cls,
        parent: Dir,
        *,
        distro: Distro,
        specfiles: list[Path] | None = None,
    ) -> ARPASourceDir:  # TODO: Self from python 3.11+
        if specfiles is None:
            specfiles = cls.locate_specfiles(parent.path)
        if not specfiles:
            raise Fail(f"{parent.path}: no specfiles found in well-known locations")
        if len(specfiles) > 1:
            raise Fail(f"{parent.path}: {len(specfiles)} specfiles found")
        return ARPASourceDir(**parent.derive_kwargs(distro=distro, specfile_path=specfiles[0]))

    @classmethod
    def prepare_from_git(
        cls,
        parent: Dir,
        *,
        distro: Distro,
        specfiles: list[Path] | None = None,
    ) -> ARPASourceGit:  # TODO: Self from python 3.11+
        if specfiles is None:
            specfiles = cls.locate_specfiles(parent.path)
        if not specfiles:
            raise Fail(f"{parent.path}: no specfiles found in well-known locations")
        if len(specfiles) > 1:
            raise Fail(f"{parent.path}: {len(specfiles)} specfiles found")
        return ARPASourceGit(**parent.derive_kwargs(distro=distro, specfile_path=specfiles[0]))


class ARPASourceDir(ARPASource, Dir):
    """
    ARPA/SIMC source directory, building RPM packages using the logic
    previously configured for travis
    """


class ARPASourceGit(ARPASource, Git):
    """
    ARPA/SIMC git repository, building RPM packages using the logic previously
    configured for travis
    """

    def lint_find_upstream_tag(self) -> git.refs.symbolic.SymbolicReference | None:
        version, release = self.spec_versions
        if version is None:
            return None
        if (tag := self.tags_by_name.get(f"v{version}")) is not None:
            return tag
        return self.tags_by_name.get(f"v{version}-1")

    def lint_find_packaging_tag(self) -> git.refs.symbolic.SymbolicReference | None:
        version, release = self.spec_versions
        if release is None:
            return None
        if (tag := self.tags_by_name.get(f"v{release}")) is not None:
            return tag
        return None

    def lint_find_packaging_branch(self) -> git.refs.symbolic.SymbolicReference | None:
        return self.repo.active_branch
