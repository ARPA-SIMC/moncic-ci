from __future__ import annotations

import itertools
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type, Union

from .. import lint
from ..exceptions import Fail
from .inputsource import URL, InputSource, LocalDir, LocalGit
from .source import Source, GitSource, register

if TYPE_CHECKING:
    from ..build import Build
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
                    f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}")


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
