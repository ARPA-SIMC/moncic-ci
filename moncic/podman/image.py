import logging
import re
from typing import TYPE_CHECKING, Optional, override

from moncic.distro import Distro
from moncic.image import BootstrappableImage, ImageType, RunnableImage

if TYPE_CHECKING:
    import podman

    from moncic.container import Container, ContainerConfig, MaintenanceContainer

    from .container import PodmanContainer
    from .images import PodmanImages

log = logging.getLogger("nspawn")


re_distro = re.compile(r"(?:^|/)([^:]+)(?::|$)")


class PodmanImage(RunnableImage):
    """Podman container image."""

    images: "PodmanImages"

    def __init__(
        self,
        *,
        images: "PodmanImages",
        name: str,
        distro: Distro,
        podman_image: "podman.domain.images.Image",
        bootstrapped_from: BootstrappableImage | None = None
    ) -> None:
        super().__init__(
            images=images, image_type=ImageType.PODMAN, name=name, distro=distro, bootstrapped_from=bootstrapped_from
        )
        self.id: str = podman_image.id
        self.short_id: str = podman_image.short_id
        self.podman_image = podman_image

    def commit(self, container: "PodmanContainer") -> None:
        """Commit the container and update the image."""
        assert container.container is not None
        podman_image = container.container.commit()
        self.podman_image = podman_image
        self.id = podman_image.id
        self.short_id = podman_image.short_id
        if ":" in self.name:
            repository, tag = self.name.split(":")
        else:
            repository, tag = self.name, "latest"
        self.podman_image.tag(self.session.podman_repository + repository, tag)

    @override
    def get_backend_id(self) -> str:
        return self.id

    @override
    def remove(self) -> BootstrappableImage | None:
        podman = self.session.podman
        podman.images.remove(self.session.podman_repository + self.name)
        return self.bootstrapped_from

    @override
    def container(self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None) -> "Container":
        from moncic.container import ContainerConfig

        from .container import PodmanContainer

        return PodmanContainer(self, config=config or ContainerConfig())

    @override
    def maintenance_container(
        self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None
    ) -> "MaintenanceContainer":
        from moncic.container import ContainerConfig

        from .container import PodmanMaintenanceContainer

        return PodmanMaintenanceContainer(self, config=config or ContainerConfig())
