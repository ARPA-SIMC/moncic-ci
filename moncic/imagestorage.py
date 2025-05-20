from __future__ import annotations

import abc
import contextlib
import logging
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from moncic.context import privs

if TYPE_CHECKING:
    from .images import Images
    from .session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = Path("/var/lib/machines")


class ImageStorage(abc.ABC):
    """
    Interface for handling image storage.

    This allows to manage access to image repositories, some of which may need
    to be activated before use and deactivated after use.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @abc.abstractmethod
    @contextlib.contextmanager
    def images(self) -> Generator[Images]:
        """
        Make the image storage accessible for the duration of this context
        manager
        """

    @classmethod
    def create_default(cls, session: Session) -> ImageStorage:
        """
        Instantiate a default ImageStorage in case no path has been provided
        """
        combined = MultiImageStorage(session)
        if privs.can_regain():
            combined.add(cls.create_from_path(session, MACHINECTL_PATH))
        combined.add(cls.create_podman(session))
        return combined

    @classmethod
    def create_from_path(cls, session: Session, path: Path) -> ImageStorage:
        """
        Instantiate the right ImageStorage for a path
        """
        from .nspawn.imagestorage import NspawnImageStorage

        return NspawnImageStorage.create(session, path)

    @classmethod
    def create_podman(cls, session: Session) -> ImageStorage:
        """Instantiate a podman image storage."""
        from .podman.imagestorage import PodmanImageStorage

        return PodmanImageStorage(session)

    @classmethod
    def create_mock(cls, session: Session) -> ImageStorage:
        """
        Instantiate a default ImageStorage in case no path has been provided
        """
        from .mock.imagestorage import NspawnImageStorage

        return NspawnImageStorage.create_mock(session)


class MultiImageStorage(ImageStorage):
    """Aggregation of multiple image storages."""

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.storages: list[ImageStorage] = []

    def add(self, storage: ImageStorage) -> None:
        self.storages.append(storage)

    @contextlib.contextmanager
    def images(self) -> Generator[Images]:
        """
        Make the image storage accessible for the duration of this context
        manager
        """
        from .images import MultiImages

        with contextlib.ExitStack() as stack:
            multi_images = MultiImages(self.session)
            for storage in self.storages:
                multi_images.add(stack.enter_context(storage.images()))
            yield multi_images
