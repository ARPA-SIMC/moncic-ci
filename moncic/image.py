import abc
import enum
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .images import Images

log = logging.getLogger("image")


class ImageType(enum.StrEnum):
    """Identify an image type."""

    NSPAWN = "nspawn"
    PODMAN = "podman"


class Image(abc.ABC):
    """
    Identify an image from which systems can be started.
    """

    def __init__(self, *, images: "Images", image_type: ImageType, name: str) -> None:
        #: Images container
        self.images = images
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

    @abc.abstractmethod
    def bootstrap(self) -> None:
        """Bootstrap the image if missing."""

    @abc.abstractmethod
    def remove(self) -> None:
        """Remove the system image if it exists."""
