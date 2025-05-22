import io
import re
import logging
from typing import TYPE_CHECKING, Optional, override

from moncic.distro import DistroFamily
from moncic.image import RunnableImage, ImageType, BootstrappableImage
from moncic.utils.osrelease import parse_osrelase_contents

if TYPE_CHECKING:
    import podman

    from moncic.container import Container, ContainerConfig
    from moncic.session import Session

    from .container import PodmanContainer
    from .images import PodmanImages

log = logging.getLogger("nspawn")


re_distro = re.compile(r"(?:^|/)([^:]+)(?::|$)")


class PodmanImage(RunnableImage):
    """Podman container image."""

    images: "PodmanImages"

    def __init__(self, *, session: "Session", name: str, podman_image: "podman.domain.images.Image") -> None:
        podman = session.podman
        os_release = podman.containers.run(podman_image, ["cat", "/etc/os-release"], remove=True)
        assert isinstance(os_release, bytes)
        with io.StringIO(os_release.decode()) as fd:
            osr = parse_osrelase_contents(fd, f"{name}:/etc/os-release")
        distro = DistroFamily.from_osrelease(osr, "test")

        super().__init__(session=session, image_type=ImageType.PODMAN, name=name, distro=distro)
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
        self.podman_image.tag(self.session.podman_repository_prefix + repository, tag)

    @override
    def get_backend_id(self) -> str:
        return self.id

    @override
    def remove(self) -> BootstrappableImage | None:
        podman = self.session.podman
        podman.images.remove(self.session.podman_repository_prefix + self.name)
        return self.bootstrap_from

    @override
    def container(self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None) -> "Container":
        from .container import PodmanContainer

        return PodmanContainer(self, config=config)

    @override
    def maintenance_container(
        self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None
    ) -> "Container":
        from .container import PodmanMaintenanceContainer

        return PodmanMaintenanceContainer(self, config=config)
