import logging
from typing import TYPE_CHECKING, override

from moncic.image import Image
from moncic.images import Images

if TYPE_CHECKING:
    from moncic.session import Session

    from .image import PodmanImage

log = logging.getLogger("images")


class PodmanImages(Images):
    """Access podman images."""

    def __init__(self, session: "Session") -> None:
        self.session = session

    @override
    def image(self, name: str) -> "PodmanImage":
        """
        Return the configuration for the named system
        """
        from .image import PodmanImage

        return PodmanImage(images=self, name=name)

    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        with self.session.moncic.privs.user():
            return self.session.podman.images.exists(name)

    def list_images(self) -> list[Image]:
        """
        List the names of images found in image directories
        """
        images: list[Image] = []
        with self.session.moncic.privs.user():
            for image in self.session.podman.images.list():
                for tag in image.tags:
                    images.append(self.image(tag))
        return images

    def deduplicate(self):
        pass
