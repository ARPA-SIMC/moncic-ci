from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .image import Image
    from .session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = "/var/lib/machines"


class Images:
    """
    Manage access to a group of container images.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @abc.abstractmethod
    def list_images(self) -> list[Image]:
        """List the names of images found in image directories."""

    @abc.abstractmethod
    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""

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

    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        return any(i.has_image(name) for i in self.images)

    def list_images(self) -> list[Image]:
        """List the names of images found in image directories."""
        privs = self.session.moncic.privs
        with privs.user():
            res: list[Image] = []
            for images in self.images:
                res += images.list_images()
            return res

    def image(self, name: str) -> Image:
        """
        Return the configuration for the named system
        """
        for images in self.images:
            if images.has_image(name):
                return images.image(name)
        raise RuntimeError(f"Image {name} not found")

    def deduplicate(self):
        for images in self.images:
            images.deduplicate()
