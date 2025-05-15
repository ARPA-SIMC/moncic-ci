import contextlib
import logging
import subprocess
from typing import TYPE_CHECKING, override, Generator

from moncic.image import Image, ImageType

if TYPE_CHECKING:
    from .images import PodmanImages
    from moncic.system import System

log = logging.getLogger("nspawn")


class PodmanImage(Image):
    """Podman container image."""

    images: "PodmanImages"

    def __init__(self, *, images: "PodmanImages", name: str) -> None:
        super().__init__(images=images, image_type=ImageType.PODMAN, name=name)
        # Name of the distribution used to bootstrap this image.
        # If missing, this image needs to be created from an existing image
        self.distro: str | None = None

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
