import abc
import copy
import enum
import logging
import subprocess
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from moncic.utils.script import Script

if TYPE_CHECKING:
    from moncic.distro import Distro
    from moncic.provision.config import ContainerInfo

    from .container import Container, ContainerConfig, MaintenanceContainer
    from .images import Images

log = logging.getLogger("image")


class ImageType(enum.StrEnum):
    """Identify an image type."""

    NSPAWN = "nspawn"
    PODMAN = "podman"
    MOCK = "mock"


class Image(abc.ABC):
    """
    Identify an image from which systems can be started.
    """

    def __init__(self, *, images: "Images", name: str, distro: "Distro", bootstrapped: bool = False) -> None:
        #: Moncic-CI session
        self.session = images.session
        #: Containing Images instance
        self.images = images
        #: Image name
        self.name: str = name
        #: Image distribution
        self.distro: "Distro" = distro
        #: True if the image is bootstrapped
        self.bootstrapped: bool = bootstrapped

    @cached_property
    def logger(self) -> logging.Logger:
        """
        Return a logger for this system
        """
        return logging.getLogger(f"image.{self.name}")

    def host_run(
        self, cmd: list[str], check: bool = True, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        """Run a command in the host system."""
        from .runner import Runner

        runner = Runner(self.logger, cmd, check=check, cwd=cwd)
        return runner.run()


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
    def __init__(
        self,
        *,
        images: "Images",
        image_type: ImageType,
        name: str,
        distro: "Distro",
        bootstrapped_from: BootstrappableImage | None = None,
    ) -> None:
        super().__init__(images=images, name=name, distro=distro, bootstrapped=True)
        #: Container type
        self.image_type: ImageType = image_type
        self.bootstrapped_from = bootstrapped_from

    def get_container_info(self) -> "ContainerInfo":
        """Get the ContainerInfo configuration for this image."""
        from moncic.provision.config import ContainerInfo
        from moncic.provision.image import ConfiguredImage

        match self.bootstrapped_from:
            case ConfiguredImage():
                return self.bootstrapped_from.config.container_info
            case _:
                return ContainerInfo()

    @abc.abstractmethod
    def get_backend_id(self) -> str:
        """Return how the image is called in the backend."""

    def update_container(self, container: "MaintenanceContainer") -> None:
        """
        Run update machinery on a container.
        """
        from moncic.runner import UserConfig

        if self.bootstrapped_from is None:
            return

        # Forward users if needed
        for u in self.bootstrapped_from.forwards_users:
            container.forward_user(UserConfig.from_user(u), allow_maint=True)

        script = Script("Upgrade container", cwd=Path("/"), user=UserConfig.root())
        self.distro.get_setup_network_script(script)
        for text in self.bootstrapped_from.maintscripts:
            for line in text.splitlines():
                script.run_unquoted(line)
        self.distro.get_update_pkgdb_script(script)
        self.distro.get_upgrade_system_script(script)
        self.distro.get_install_packages_script(script, sorted(self.bootstrapped_from.package_list))
        container.run_script(script)

    def update(self) -> None:
        """Run periodic maintenance on the system."""
        with self.maintenance_container() as container:
            self.update_container(container)

    @abc.abstractmethod
    def remove(self) -> BootstrappableImage | None:
        """Remove the system image if it exists."""

    def describe(self) -> dict[str, Any]:
        """Return a dictionary describing facts about the image."""
        return {}

    def _make_container_config(self, config: Optional["ContainerConfig"]) -> "ContainerConfig":
        """Create the final container config."""
        if config is None:
            config = ContainerConfig()
        else:
            config = copy.deepcopy(config)
        self.distro.container_config_hook(self, config)
        return config

    @abc.abstractmethod
    def container(self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None) -> "Container":
        """
        Boot a container with this system
        """

    @abc.abstractmethod
    def maintenance_container(
        self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None
    ) -> "MaintenanceContainer":
        """
        Boot a container with this system
        """
