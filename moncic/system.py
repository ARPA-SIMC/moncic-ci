import abc
from functools import cached_property
from typing import Any, TYPE_CHECKING

from .image import Image

if TYPE_CHECKING:
    from .distro import Distro
    from .imagestorage import Images


class System(abc.ABC):
    """
    A system configured in the CI.

    System objects hold the system configuration and contain factory methods to
    instantiate objects used to work with, and maintain, the system
    """

    def __init__(self, images: "Images", image: Image) -> None:
        self.images = images
        self.image = image
        self.log = image.logger

    def __str__(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        return self.image.name

    @abc.abstractmethod
    def _get_distro(self) -> "Distro":
        """Return the distribution this system is based on."""

    @cached_property
    def distro(self) -> "Distro":
        """Return the distribution this system is based on."""
        return self._get_distro()

    @abc.abstractmethod
    def is_bootstrapped(self) -> bool:
        """Check if the image has been bootstrapped."""

    @abc.abstractmethod
    def describe_container(self) -> dict[str, Any]:
        """Return a dictionary describing facts about the container."""
