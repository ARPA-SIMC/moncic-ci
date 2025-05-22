import contextlib
import io
import logging
import subprocess
from typing import TYPE_CHECKING, Optional, override
from collections.abc import Generator

from moncic.distro import DistroFamily
from moncic.image import Image, ImageType
from moncic.utils.osrelease import parse_osrelase_contents

if TYPE_CHECKING:
    from moncic.container import Container, ContainerConfig

    from .container import PodmanContainer
    from .images import PodmanImages

log = logging.getLogger("nspawn")


class PodmanImage(Image):
    """Podman container image."""

    images: "PodmanImages"

    def __init__(self, *, images: "PodmanImages", name: str) -> None:
        podman = images.session.podman
        image = podman.images.get(images.repository_prefix + name)
        os_release = podman.containers.run(image, ["cat", "/etc/os-release"], remove=True)
        assert isinstance(os_release, bytes)
        with io.StringIO(os_release.decode()) as fd:
            osr = parse_osrelase_contents(fd, f"{name}:/etc/os-release")
        distro = DistroFamily.from_osrelease(osr, "test")

        super().__init__(images=images, image_type=ImageType.PODMAN, name=name, distro=distro, bootstrapped=True)
        self.id: str = image.id
        self.short_id: str = image.short_id
        self.podman_image = image

    def commit(self, container: "PodmanContainer") -> None:
        """Commit the container and update the image."""
        assert container.container is not None
        podman_image = container.container.commit()
        self.podman_image = podman_image
        self.id = podman_image.id
        self.short_id = podman_image.short_id
        repository, tag = self.name.split(":")
        self.podman_image.tag(self.images.repository_prefix + repository, tag)

    @override
    def get_backend_id(self) -> str:
        return self.id

    def local_run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        """
        Run a command on the host system.
        """
        raise NotImplementedError()

    @override
    def remove_config(self) -> None:
        raise NotImplementedError()

    @override
    def bootstrap(self) -> None:
        raise NotImplementedError()

    @override
    def update(self) -> None:
        raise NotImplementedError()

    @override
    def remove(self) -> None:
        raise NotImplementedError()

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
