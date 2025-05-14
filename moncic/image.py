import enum
import logging

log = logging.getLogger("image")


class ImageType(enum.StrEnum):
    """Identify an image type."""

    NSPAWN = "nspawn"
    PODMAN = "podman"


class Image:
    """
    Identify an image from which systems can be started.
    """

    def __init__(self, *, image_type: ImageType, name: str) -> None:
        #: Container type
        self.image_type: ImageType = image_type
        #: Image name
        self.name: str = name

    @property
    def logger(self):
        """
        Return a logger for this system
        """
        return logging.getLogger(f"system.{self.name}")
