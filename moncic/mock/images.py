import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, override

from moncic.distro import DistroFamily
from moncic.image import BootstrappableImage, RunnableImage
from moncic.images import BootstrappingImages

if TYPE_CHECKING:
    from .image import MockImage
    from .session import MockSession


class MockImages(BootstrappingImages):
    """
    Mock image storage, used for testing
    """

    session: "MockSession"

    @override
    def get_logger(self) -> logging.Logger:
        return logging.getLogger("images.mock")

    @override
    def host_run(
        self, cmd: list[str], check: bool = True, cwd: Path | None = None, interactive: bool = False
    ) -> subprocess.CompletedProcess:
        self.session.run_log.append(cmd, {})
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    @override
    def list_images(self) -> list[str]:
        return []

    @override
    def has_image(self, name: str) -> bool:
        return True

    @override
    def image(self, name: str) -> "MockImage":
        from .image import MockImage

        return MockImage(session=self.session, name=name, distro=DistroFamily.lookup_distro(name))

    @override
    def bootstrap(self, image: BootstrappableImage) -> RunnableImage:
        from moncic.provision.image import ConfiguredImage, DistroImage

        path = Path("/test")
        match image:
            case ConfiguredImage():
                self.session.run_log.append_action(f"{image.name}: extend parent")
            case DistroImage():
                image.distro.bootstrap(self, path)
            case _:
                raise NotImplementedError
        return self.image(image.name)
