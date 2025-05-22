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
        self.repository_prefix = "localhost/moncic-ci/"

    @override
    def image(self, name: str) -> "PodmanImage":
        """
        Return the configuration for the named system
        """
        from .image import PodmanImage

        return PodmanImage(images=self, name=name)

    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        return self.session.podman.images.exists(name)

    def list_images(self) -> list[Image]:
        """
        List the names of images found in image directories
        """
        images: list[Image] = []
        for image in self.session.podman.images.list(name=self.repository_prefix + "*"):
            for tag in image.tags:
                if not tag.startswith(self.repository_prefix):
                    continue
                images.append(self.image(tag.removeprefix(self.repository_prefix)))
        return images

    def deduplicate(self):
        pass
