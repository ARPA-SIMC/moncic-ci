from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .source import URL, InputSource, LocalDir, LocalGit, Source, register

if TYPE_CHECKING:
    from ..build import Builder

log = logging.getLogger(__name__)


@dataclass
class RPMGit(Source):
    """
    Git working directory with a Debian package
    """
    # TODO: locate specfile


@register
@dataclass
class ARPAGit(RPMGit):
    """
    ARPA/SIMC git repository, building RPM packages using the logic previously
    configured for travis
    """
    NAME = "rpm-arpa"

    @classmethod
    def _create_from_repo(cls, builder: Builder, source: LocalGit) -> "RPMGit":
        return cls(source, source.repo.working_dir)

    @classmethod
    def create(cls, builder: Builder, source: InputSource) -> "ARPAGit":
        if isinstance(source, LocalGit):
            return cls(source.source, source.repo.working_dir)
        elif isinstance(source, URL):
            return cls.create(builder, source.clone(builder))
        if isinstance(source, LocalDir):
            return cls(source.source, source.path)
        else:
            raise RuntimeError(
                    f"cannot create {cls.__name__} instances from an input source of type {source.__class__.__name__}")
