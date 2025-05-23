import contextlib
import logging
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from moncic.imagestorage import ImageStorage
from moncic.utils.btrfs import is_btrfs

from .images import BtrfsImages, BtrfsMachinectlImages, PlainImages, PlainMachinectlImages

if TYPE_CHECKING:
    from moncic.images import Images
    from moncic.session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = Path("/var/lib/machines")


class NspawnImageStorage(ImageStorage):
    """Image storage for nspawn images."""

    @classmethod
    def create(cls, session: "Session", path: Path) -> ImageStorage:
        """
        Instantiate the right ImageStorage for a path
        """
        if path.is_dir():
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


class PlainImageStorage(NspawnImageStorage):
    """
    Store images in a non-btrfs directory
    """

    def __init__(self, session: "Session", imagedir: Path):
        super().__init__(session)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator["Images", None, None]:
        yield PlainImages(self.session, self.imagedir)


class BtrfsImageStorage(NspawnImageStorage):
    """
    Store images in a btrfs directory
    """

    def __init__(self, session: "Session", imagedir: Path):
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
        yield PlainMachinectlImages(self.session)


class BtrfsMachineImageStorage(BtrfsImageStorage):
    """
    Store images in /var/lib/machines in a way that is compatibile with
    machinectl
    """

    def __init__(self, session: "Session"):
        super().__init__(session, MACHINECTL_PATH)

    @contextlib.contextmanager
    def images(self) -> Generator["Images", None, None]:
        yield BtrfsMachinectlImages(self.session)
