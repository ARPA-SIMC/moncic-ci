from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Type, Union

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
    # TODO: locate specfile
    @classmethod
    def detect(cls, distro: Distro, source: Union[LocalGit, LocalDir]) -> "RPMSource":
        travis_yml = os.path.join(source.path, ".travis.yml")
        try:
            with open(travis_yml, "rt") as fd:
                if 'simc/stable' in fd.read():
                    return ARPASource._create_from_repo(source)
        except FileNotFoundError:
            pass
        raise Fail("simc/stable not found in .travis.yml for ARPA builds")


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
