from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Generator
from typing import TYPE_CHECKING

from .utils.btrfs import is_btrfs
from .nspawn.images import Images, MockImages, PlainImages, BtrfsImages

if TYPE_CHECKING:
    from .session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = "/var/lib/machines"


class ImageStorage:
    """
    Interface for handling image storage
    """

    def __init__(self, session: Session):
        self.session = session

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        """
        Make the image storage available as a directory, for the duration of
        this context manager
        """
        raise NotImplementedError(f"{self.__class__.__name__}.imagedir is not implemented")

    @classmethod
    def create(cls, session: Session, path: str) -> ImageStorage:
        """
        Instantiate the right ImageStorage for a path
        """
        if os.path.isdir(path):
            if path == MACHINECTL_PATH:
                if is_btrfs(path):
                    return BtrfsMachineImageStorage(session)
                else:
                    return PlainMachineImageStorage(session)
            else:
                if is_btrfs(path):
                    return BtrfsImageStorage(session, path)
                else:
                    return PlainImageStorage(session, path)
        else:
            raise RuntimeError(f"images path {path!r} does not point to a directory")

    @classmethod
    def create_default(cls, session: Session) -> ImageStorage:
        """
        Instantiate a default ImageStorage in case no path has been provided
        """
        return cls.create(session, MACHINECTL_PATH)

    @classmethod
    def create_mock(cls, session: Session) -> ImageStorage:
        """
        Instantiate a default ImageStorage in case no path has been provided
        """
        return MockImageStorage(session, MACHINECTL_PATH)


class MockImageStorage(ImageStorage):
    """
    Store images in a non-btrfs directory
    """

    def __init__(self, session: Session, imagedir: str):
        super().__init__(session)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        yield MockImages(self.session, self.imagedir)


class PlainImageStorage(ImageStorage):
    """
    Store images in a non-btrfs directory
    """

    def __init__(self, session: Session, imagedir: str):
        super().__init__(session)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        yield PlainImages(self.session, self.imagedir)


class BtrfsImageStorage(ImageStorage):
    """
    Store images in a btrfs directory
    """

    def __init__(self, session: Session, imagedir: str):
        super().__init__(session)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        yield BtrfsImages(self.session, self.imagedir)


class PlainMachineImageStorage(PlainImageStorage):
    """
    Store images in /var/lib/machines in a way that is compatibile with
    machinectl
    """

    def __init__(self, session: Session):
        super().__init__(session, MACHINECTL_PATH)

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        yield Images(self.session, self.imagedir)


class BtrfsMachineImageStorage(BtrfsImageStorage):
    """
    Store images in /var/lib/machines in a way that is compatibile with
    machinectl
    """

    def __init__(self, session: Session):
        super().__init__(session, MACHINECTL_PATH)

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        yield BtrfsImages(self.session, self.imagedir)
