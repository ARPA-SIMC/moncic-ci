from __future__ import annotations

import glob
import itertools
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Type, Union

from .. import lint
from ..exceptions import Fail
from .inputsource import URL, InputSource, LocalDir, LocalGit
from .source import Source, register

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
        return ARPASource._create_from_repo(source)


@register
@dataclass
class ARPASource(RPMSource):
    """
    ARPA/SIMC git repository, building RPM packages using the logic previously
    configured for travis
    """
    NAME = "rpm-arpa"

    @classmethod
    def _create_from_repo(cls, source: Union[LocalGit, LocalDir]) -> "ARPASource":
        return cls(source, source.path)

    @classmethod
    def create(cls, distro: Distro, source: InputSource) -> "ARPASource":
        if isinstance(source, (LocalGit, LocalDir)):
            return cls(source, source.path)
        elif isinstance(source, URL):
            return cls.create(source.clone())
        else:
            raise RuntimeError(
                    f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}")

    def get_build_class(self) -> Type["Build"]:
        from ..build.arpa import ARPA
        return ARPA

    def get_linter_class(self) -> Type["lint.Linter"]:
        return lint.ARPALinter

    def locate_specfile(self) -> str:
        srcdir = self.host_path
        spec_globs = ["fedora/SPECS/*.spec", "*.spec"]
        specs = list(itertools.chain.from_iterable(glob.glob(os.path.join(srcdir, g)) for g in spec_globs))

        if not specs:
            raise Fail("Spec file not found")

        if len(specs) > 1:
            raise Fail(f"{len(specs)} .spec files found")

        return os.path.relpath(specs[0], start=srcdir)
