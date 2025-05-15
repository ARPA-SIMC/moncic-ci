import logging
from typing import TYPE_CHECKING, override

from moncic.images import Images

if TYPE_CHECKING:
    from moncic.session import Session
    from .image import PodmanImage

log = logging.getLogger("images")


class PodmanImages(Images):
    """Access podman images."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @override
    def image(self, name: str) -> "PodmanImage":
        """
        Return the configuration for the named system
        """
        raise NotImplementedError()

    def list_images(self, skip_unaccessible: bool = False) -> list[str]:
        """
        List the names of images found in image directories
        """
        return []

    def deduplicate(self):
        pass
