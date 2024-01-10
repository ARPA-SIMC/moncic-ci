from __future__ import annotations

import itertools
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type, Union

from .. import lint
from ..container import ContainerConfig
from ..exceptions import Fail
from .inputsource import LocalDir, LocalGit
from .source import GitSource, Source, register

if TYPE_CHECKING:
    from ..build import Build
    from ..container import System
    from ..distro import Distro

log = logging.getLogger(__name__)


@dataclass
class RPMSource(Source):
    """
    Git working directory with a Debian package
    """

    specfile_path: Optional[str] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.specfile_path is None:
            self.specfile_path = self.locate_specfile()

    def locate_specfile(self) -> str:
        """
        Locate the specfile inside the source directory.

        Return its path relative to the sources root
        """
        raise NotImplementedError(f"{self.__class__.__name__}.locate_specfile() is not implemented")

    @classmethod
    def detect(cls, distro: Distro, source: Union[LocalGit, LocalDir]) -> "RPMSource":
        if isinstance(source, LocalGit):
            return ARPAGitSource._create_from_repo(source)
        elif isinstance(source, LocalDir):
            return ARPASource._create_from_repo(source)
        else:
            raise RuntimeError(
                f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}"
            )


class ARPASourceMixin(RPMSource):
    """
    Base class for ARPA sources
    """

    def get_build_class(self) -> Type["Build"]:
        from ..build.arpa import ARPA

        return ARPA

    def get_linter_class(self) -> Type["lint.Linter"]:
        return lint.ARPALinter

    def locate_specfile(self) -> str:
        srcdir = self.host_path
        spec_globs = ["fedora/SPECS/*.spec", "*.spec"]
        specs = list(itertools.chain.from_iterable(srcdir.glob(g) for g in spec_globs))

        if not specs:
            raise Fail("Spec file not found")

        if len(specs) > 1:
            raise Fail(f"{len(specs)} .spec files found")

        return os.path.relpath(specs[0], start=srcdir)

    def find_versions(self, system: System) -> dict[str, str]:
        versions = super().find_versions(system)

        spec_path = self.specfile_path

        # Run in container: rpmspec --parse file.spec
        if (self.host_path / spec_path).exists():
            cconfig = ContainerConfig()
            cconfig.configure_workdir(self.host_path, bind_type="ro")
            with system.create_container(config=cconfig) as container:
                res = container.run(["/usr/bin/rpmspec", "--parse", spec_path])
            if res.returncode == 0:
                version: Optional[str] = None
                release: Optional[str] = None
                for line in res.stdout.splitlines():
                    if line.startswith(b"Version:"):
                        if version is None:
                            version = line[8:].strip().decode()
                    if line.startswith(b"Release:"):
                        if release is None:
                            release = line[8:].strip().decode()

                if version is not None:
                    versions["spec-upstream"] = version
                    if release is not None:
                        versions["spec-release"] = version + "-" + release

        return versions


@register
@dataclass
class ARPASource(ARPASourceMixin, RPMSource):
    """
    ARPA/SIMC source directory, building RPM packages using the logic
    previously configured for travis
    """

    NAME = "rpm-arpa"

    @classmethod
    def _create_from_repo(cls, source: LocalDir) -> "ARPASource":
        return cls(source, Path(source.path))


@register
@dataclass
class ARPAGitSource(ARPASourceMixin, RPMSource, GitSource):
    """
    ARPA/SIMC git repository, building RPM packages using the logic previously
    configured for travis
    """

    NAME = "rpm-arpa-git"

    @classmethod
    def _create_from_repo(cls, source: LocalGit) -> "ARPAGitSource":
        return cls(source, Path(source.path))

    def _check_arpa_commits(self, linter: "lint.Linter"):
        repo = self.source.repo

        # Get the latest version tag
        res = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "--match=v[0-9]*"],
            cwd=repo.working_dir,
            text=True,
            capture_output=True,
            check=True,
        )
        last_tag = res.stdout.strip()

        if "-" not in last_tag:
            return

        # Look for a previous version tag
        uv, rv = last_tag[1:].split("-", 1)
        if (last_ver := int(rv)) == 1:
            return

        prev_ver: Optional[int] = None
        prev_tag: Optional[str] = None
        prefix = f"v{uv}-"
        for tag in repo.tags:
            if tag.name.startswith(prefix):
                ver = int(tag.name[len(prefix) :])
                if ver < last_ver:
                    if prev_ver is None or prev_ver < ver:
                        prev_ver = ver
                        prev_tag = tag.name

        if prev_ver is None:
            linter.warning(f"Found tag {last_tag} but no earlier release tag for the same upstream version")
            return

        # Check that the diff between the two tags only affects files under the
        # same directory as the specfile
        changes_root = os.path.dirname(self.specfile_path)
        prev = repo.commit(prev_tag)
        last = repo.commit(last_tag)
        upstream_affected: set[str] = set()
        for diff in prev.diff(last):
            if diff.a_path is not None and not diff.a_path.startswith(changes_root):
                upstream_affected.add(diff.a_path)
            if diff.b_path is not None and not diff.b_path.startswith(changes_root):
                upstream_affected.add(diff.b_path)

        for name in sorted(upstream_affected):
            linter.warning(f"{name}: upstream file affected by packaging changes")

    def lint(self, linter: "lint.Linter"):
        super().lint(linter)
        self._check_arpa_commits(linter)
