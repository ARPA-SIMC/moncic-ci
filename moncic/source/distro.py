from __future__ import annotations

import abc
from typing import TYPE_CHECKING
from pathlib import Path

from .source import Source
from .local import File, Dir, Git
from ..utils.guest import host_only

if TYPE_CHECKING:
    from ..distro import Distro
    from .local import LocalSource


class DistroSource(Source, abc.ABC):
    """
    Distribution-aware source
    """

    distro: Distro

    def __init__(self, *, distro: Distro, **kwargs) -> None:
        super().__init__(**kwargs)
        self.distro = distro

    @host_only
    def collect_build_artifacts(self, destdir: Path, artifact_dir: Path | None = None) -> None:
        """
        Gather build artifacts host system and copy them to the target directory.

        :param:artifact_dir:if provided, it is an extra possible source of artifacts
        """
        # Do nothing by default
        pass

    @classmethod
    @abc.abstractmethod
    def create_from_file(cls, parent: File, *, distro: Distro) -> "DistroSource":
        """Create a distro-specific source from a File."""

    @classmethod
    @abc.abstractmethod
    def create_from_dir(cls, parent: Dir, *, distro: Distro) -> "DistroSource":
        """Create a distro-specific source from a Dir directory."""

    @classmethod
    @abc.abstractmethod
    def create_from_git(cls, parent: Git, *, distro: Distro) -> "DistroSource":
        """Create a distro-specific source from a Git repo."""

    @classmethod
    def create_from_local(cls, parent: LocalSource, *, distro: Distro, style: str | None = None) -> "DistroSource":
        """Create a distro-specific source from a local source."""
        # TODO: redo with a match on python 3.10+
        if isinstance(parent, Git):
            return cls.create_from_git(parent, distro=distro)
        elif isinstance(parent, Dir):
            return cls.create_from_dir(parent, distro=distro)
        elif isinstance(parent, File):
            return cls.create_from_file(parent, distro=distro)
        else:
            raise NotImplementedError(f"Local source type {parent.__class__} not supported")
