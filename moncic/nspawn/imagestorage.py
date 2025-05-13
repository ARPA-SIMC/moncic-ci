import contextlib
import logging
import os
from collections.abc import Generator
from typing import TYPE_CHECKING

from moncic.utils.btrfs import is_btrfs
from moncic.imagestorage import ImageStorage
from .images import PlainImages, BtrfsImages

if TYPE_CHECKING:
    from moncic.session import Session
    from moncic.images import Images

log = logging.getLogger("images")

MACHINECTL_PATH = "/var/lib/machines"


class NspawnImageStorage(ImageStorage):
    """Image storage for nspawn images."""

    @classmethod
    def create(cls, session: "Session", path: str) -> ImageStorage:
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
    def create_mock(cls, session: "Session") -> ImageStorage:
        """
        Instantiate a default ImageStorage in case no path has been provided
        """
        return MockImageStorage(session, MACHINECTL_PATH)


class MockImageStorage(NspawnImageStorage):
    """
    Store images in a non-btrfs directory
    """

    def __init__(self, session: "Session", imagedir: str):
        super().__init__(session)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator["Images", None, None]:
        yield MockImages(self.session, self.imagedir)


class PlainImageStorage(NspawnImageStorage):
    """
    Store images in a non-btrfs directory
    """

    def __init__(self, session: "Session", imagedir: str):
        super().__init__(session)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator["Images", None, None]:
        yield PlainImages(self.session, self.imagedir)


class BtrfsImageStorage(NspawnImageStorage):
    """
    Store images in a btrfs directory
    """

    def __init__(self, session: "Session", imagedir: str):
        super().__init__(session)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator["Images", None, None]:
        yield BtrfsImages(self.session, self.imagedir)


class PlainMachineImageStorage(PlainImageStorage):
    """
    Store images in /var/lib/machines in a way that is compatibile with
    machinectl
    """

    def __init__(self, session: "Session"):
        super().__init__(session, MACHINECTL_PATH)

    @contextlib.contextmanager
    def images(self) -> Generator["Images", None, None]:
        yield Images(self.session, self.imagedir)


class BtrfsMachineImageStorage(BtrfsImageStorage):
    """
    Store images in /var/lib/machines in a way that is compatibile with
    machinectl
    """

    def __init__(self, session: "Session"):
        super().__init__(session, MACHINECTL_PATH)

    @contextlib.contextmanager
    def images(self) -> Generator["Images", None, None]:
        yield BtrfsImages(self.session, self.imagedir)
