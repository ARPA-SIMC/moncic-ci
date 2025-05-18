import io
import contextlib
import logging
import subprocess
from typing import TYPE_CHECKING, override, Generator

from moncic.image import Image, ImageType
from moncic.distro import DistroFamily
from moncic.utils.osrelease import parse_osrelase_contents

if TYPE_CHECKING:
    from .images import PodmanImages
    from moncic.system import System

log = logging.getLogger("nspawn")


class PodmanImage(Image):
    """Podman container image."""

    images: "PodmanImages"

    def __init__(self, *, images: "PodmanImages", name: str) -> None:
        podman = images.session.podman
        image = podman.images.get(name)
        os_release = podman.containers.run(image.id, ["cat", "/etc/os-release"])
        assert isinstance(os_release, bytes)
        with io.StringIO(os_release.decode()) as fd:
            osr = parse_osrelase_contents(fd, f"{name}:/etc/os-release")
        distro = DistroFamily.from_osrelease(osr, "test")

        super().__init__(images=images, image_type=ImageType.PODMAN, name=name, distro=distro, bootstrapped=True)
        self.id: str = image.id
        self.short_id: str = image.short_id

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
    @contextlib.contextmanager
    def system(self) -> Generator["System", None, None]:
        raise NotImplementedError()

    @override
    def bootstrap(self) -> None:
        raise NotImplementedError()

    @override
    def remove(self) -> None:
        raise NotImplementedError()
