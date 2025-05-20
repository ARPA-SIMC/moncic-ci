import contextlib
from collections.abc import Generator
from typing import TYPE_CHECKING

from moncic.nspawn.imagestorage import NspawnImageStorage

from .images import MockImages

if TYPE_CHECKING:
    from moncic.images import Images
    from moncic.session import Session


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
