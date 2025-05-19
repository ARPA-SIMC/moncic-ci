import abc
import enum
import logging
from typing import TYPE_CHECKING, ContextManager, Any

from moncic.distro import Distro

if TYPE_CHECKING:
    from .images import Images
    from .system import System

log = logging.getLogger("image")


class ImageType(enum.StrEnum):
    """Identify an image type."""

    NSPAWN = "nspawn"
    PODMAN = "podman"


class Image(abc.ABC):
    """
    Identify an image from which systems can be started.
    """

    def __init__(
        self, *, images: "Images", image_type: ImageType, name: str, distro: Distro, bootstrapped: bool
    ) -> None:
        #: Images container
        self.images = images
        #: Container type
        self.image_type: ImageType = image_type
        #: Image name
        self.name: str = name
        #: Image distribution
        self.distro: Distro = distro
        #: True if the image is bootstrapped
        self.bootstrapped: bool = bootstrapped

    @property
    def logger(self):
        """
        Return a logger for this system
        """
        return logging.getLogger(f"system.{self.name}")

    @abc.abstractmethod
    def get_backend_id(self) -> str:
        """Return how the image is called in the backend."""

    @abc.abstractmethod
    def bootstrap(self) -> None:
        """Bootstrap the image if missing."""

    @abc.abstractmethod
    def remove(self) -> None:
        """Remove the system image if it exists."""

    @abc.abstractmethod
    def remove_config(self) -> None:
        """Remove the configuration file, if it exists."""

    @abc.abstractmethod
    def system(self) -> ContextManager["System"]:
        """
        Instantiate a System that can only be used for the duration
        of this context manager.
        """
        # TODO: move to Image

    def describe_container(self) -> dict[str, Any]:
        """Return a dictionary describing facts about the container."""
        return {}
