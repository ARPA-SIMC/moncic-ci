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
    Image storage made available as a directory in the file system
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @abc.abstractmethod
    def list_images(self, skip_unaccessible: bool = False) -> list[str]:
        """List the names of images found in image directories."""

    @abc.abstractmethod
    def system_config(self, name: str) -> Image:
        """
        Return the configuration for the named system
        """
        # TODO: rename to image(name: str)

    @abc.abstractmethod
    def system(self, name: str) -> ContextManager[System]:
        """
        Instantiate a System that can only be used for the duration
        of this context manager.
        """
        # TODO: move to Image

    @abc.abstractmethod
    def maintenance_system(self, name: str) -> ContextManager[MaintenanceSystem]:
        """
        Instantiate a MaintenanceSystem that can only be used for the duration
        of this context manager.

        This allows maintenance to be transactional, limited to backends that
        support it, so that errors in the maintenance roll back to the previous
        state and do not leave an inconsistent OS image
        """
        # TODO: move to Image

    @abc.abstractmethod
    def bootstrap_system(self, name: str):
        """
        Bootstrap the given system if missing
        """
        # TODO: move to Image

    @abc.abstractmethod
    def remove_system(self, name: str):
        """
        Remove the named system if it exists
        """
        # TODO: move to Image

    @abc.abstractmethod
    def find_config(self, name: str) -> str | None:
        """
        Return the path of the config file of the given image, if it exists
        """
        # TODO: rename to find_image

    @abc.abstractmethod
    def remove_config(self, name: str):
        """
        Remove the configuration for the named system, if it exists
        """
        # TODO: move the remove method to Image

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

    @abc.abstractmethod
    def system_config(self, name: str) -> Image:
        """
        Return the configuration for the named system
        """
        raise NotImplementedError(f"{self.__class__.__name__}.system_config is not implemented")

    @abc.abstractmethod
    def system(self, name: str) -> ContextManager[System]:
        """
        Instantiate a System that can only be used for the duration
        of this context manager.
        """

    @abc.abstractmethod
    def maintenance_system(self, name: str) -> ContextManager[MaintenanceSystem]:
        """
        Instantiate a MaintenanceSystem that can only be used for the duration
        of this context manager.

        This allows maintenance to be transactional, limited to backends that
        support it, so that errors in the maintenance roll back to the previous
        state and do not leave an inconsistent OS image
        """

    @abc.abstractmethod
    def bootstrap_system(self, name: str):
        """
        Bootstrap the given system if missing
        """

    @abc.abstractmethod
    def remove_system(self, name: str):
        """
        Remove the named system if it exists
        """

    @abc.abstractmethod
    def find_config(self, name: str) -> str | None:
        """
        Return the path of the config file of the given image, if it exists
        """

    def remove_config(self, name: str):
        """
        Remove the configuration for the named system, if it exists
        """
        # Import here to prevent import loops
        if path := self.find_config(name):
            log.info("%s: removing image configuration file", path)
            os.unlink(path)

    def deduplicate(self):
        for images in self.images:
            images.deduplicate()
