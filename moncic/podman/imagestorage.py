import contextlib
import logging
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from moncic.imagestorage import ImageStorage

from .images import PodmanImages

if TYPE_CHECKING:
    from moncic.images import Images
    from moncic.session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = Path("/var/lib/machines")


class PodmanImageStorage(ImageStorage):
    """Image storage for podman images."""

    def __init__(self, session: "Session") -> None:
        super().__init__(session)

    @contextlib.contextmanager
    def images(self) -> Generator["Images", None, None]:
        yield PodmanImages(self.session)

    @classmethod
    def create(cls, session: "Session") -> ImageStorage:
        """Instantiate an image storage for podman images."""
        return cls(session)
