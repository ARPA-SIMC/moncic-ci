import abc
import enum
import logging
from functools import cached_property
from typing import TYPE_CHECKING, Any, Optional

from moncic.distro import Distro

if TYPE_CHECKING:
    from moncic.provision.config import ContainerInfo
    from moncic.provision.image import ConfiguredImage
    from .container import Container, ContainerConfig
    from .session import Session

log = logging.getLogger("image")


class ImageType(enum.StrEnum):
    """Identify an image type."""

    NSPAWN = "nspawn"
    PODMAN = "podman"


class Image(abc.ABC):
    """
    Identify an image from which systems can be started.
    """

    def __init__(self, *, session: "Session", name: str, distro: Distro, bootstrapped: bool = False) -> None:
        #: Moncic-CI session
        self.session = session
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
        return logging.getLogger(f"image.{self.name}")


class BootstrappableImage(Image, abc.ABC):
    """Image that can be bootstrapped but not run."""

    @cached_property
    def forwards_users(self) -> list[str]:
        """List users forwarded to this container."""
        return self._forwards_users()

    def _forwards_users(self) -> list[str]:
        """List users forwarded to this container."""
        return []

    @cached_property
    def package_list(self) -> set[str]:
        """List all packages to be installed in the image."""
        return self.distro_package_list | self.config_package_list

    @cached_property
    def distro_package_list(self) -> set[str]:
        """List packages required by distribution descriptions."""
        return self._distro_package_list()

    def _distro_package_list(self) -> set[str]:
        return set()

    @cached_property
    def config_package_list(self) -> set[str]:
        """List packages required by image configuration."""
        return self._config_package_list()

    def _config_package_list(self) -> set[str]:
        return set()

    @cached_property
    def maintscripts(self) -> list[str]:
        """
        Build a script with the concatenation of all scripts coming from
        calling distro.get_{name}_script on all the containers in the chain
        """
        return self._maintscripts()

    def _maintscripts(self) -> list[str]:
        return []

    def bootstrap(self) -> "RunnableImage":
        """Bootstrap the image if missing."""
        return self.session.bootstrapper.bootstrap(self)

    @abc.abstractmethod
    def remove_config(self) -> None:
        """Remove the configuration file, if it exists."""


class RunnableImage(Image, abc.ABC):
    def __init__(self, *, session: "Session", image_type: ImageType, name: str, distro: Distro) -> None:
        super().__init__(session=session, name=name, distro=distro, bootstrapped=True)
        #: Container type
        self.image_type: ImageType = image_type
        self.bootstrap_from: BootstrappableImage | None = None

    def get_container_info(self) -> "ContainerInfo":
        """Get the ContainerInfo configuration for this image."""
        match self.bootstrap_from:
            case ConfiguredImage():
                return self.bootstrap_from.config.container_info
            case _:
                return ContainerInfo()

    def set_bootstrap_from(self, image: BootstrappableImage) -> None:
        """Set the BootstrappableImage that can generate this image."""
        self.bootstrap_from = image

    @abc.abstractmethod
    def get_backend_id(self) -> str:
        """Return how the image is called in the backend."""

    @abc.abstractmethod
    def update(self):
        """Run periodic maintenance on the system."""

    @abc.abstractmethod
    def remove(self) -> BootstrappableImage | None:
        """Remove the system image if it exists."""

    def describe(self) -> dict[str, Any]:
        """Return a dictionary describing facts about the image."""
        return {}

    @abc.abstractmethod
    def container(self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None) -> "Container":
        """
        Boot a container with this system
        """

    @abc.abstractmethod
    def maintenance_container(
        self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None
    ) -> "Container":
        """
        Boot a container with this system
        """
