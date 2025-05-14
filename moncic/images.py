from __future__ import annotations

import abc
import logging
import os
from typing import TYPE_CHECKING, ContextManager


if TYPE_CHECKING:
    from .image import Image
    from .session import Session
    from .system import System

log = logging.getLogger("images")

MACHINECTL_PATH = "/var/lib/machines"


class Images:
    """
    Manage access to a group of container images.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @abc.abstractmethod
    def list_images(self, skip_unaccessible: bool = False) -> list[str]:
        """List the names of images found in image directories."""

    @abc.abstractmethod
    def image(self, name: str) -> Image:
        """
        Return the configuration for the named system
        """

    @abc.abstractmethod
    def deduplicate(self):
        """Deduplicate storage of common files (if supported)."""


class MultiImages(Images):
    """Aggregation of multiple Images."""

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.images: list[Images] = []

    def add(self, images: Images) -> None:
        self.images.append(images)

    def list_images(self, skip_unaccessible: bool = False) -> list[str]:
        """List the names of images found in image directories."""
        res: list[str] = []
        for image in self.images:
            res += image.list_images(skip_unaccessible=skip_unaccessible)
        return res

    def image(self, name: str) -> Image:
        """
        Return the configuration for the named system
        """
        # TODO: try all Images in sequence
        raise NotImplementedError()

    def deduplicate(self):
        for images in self.images:
            images.deduplicate()
