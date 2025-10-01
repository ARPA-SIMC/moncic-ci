import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, override

from moncic.image import BootstrappableImage, Image, RunnableImage
from moncic.images import BootstrappingImages

if TYPE_CHECKING:
    from .image import MockRunnableImage
    from .session import MockSession


class MockImages(BootstrappingImages):
    """
    Mock image storage, used for testing
    """

    session: "MockSession"

    def __init__(self, session: "MockSession") -> None:
        super().__init__(session)
        self.bootstrapped: dict[str, "MockRunnableImage"] = {}
        self.bootstrap_path = Path(self.session.enter_context(tempfile.TemporaryDirectory()))

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
        return name in self.bootstrapped

    @override
    def image(self, name: str, variant_of: Image | None = None) -> "MockRunnableImage":
        return self.bootstrapped[name]

    @override
    def bootstrap_new(self, image: "BootstrappableImage") -> "RunnableImage":
        from .image import MockRunnableImage

        self.session.run_log.append_action(f"{image.name}: bootstrap")
        path = self.bootstrap_path
        image.distro.bootstrap(self, path)
        bootstrapped = MockRunnableImage(images=self, name=image.name, distro=image.distro, bootstrapped_from=image)
        self.bootstrapped[image.name] = bootstrapped
        return bootstrapped

    @override
    def bootstrap_extend(self, image: "BootstrappableImage", parent: "RunnableImage") -> "RunnableImage":
        from .image import MockRunnableImage

        self.session.run_log.append_action(f"{image.name}: extend {parent.name}")
        bootstrapped = MockRunnableImage(images=self, name=image.name, distro=image.distro, bootstrapped_from=image)
        self.bootstrapped[image.name] = bootstrapped
        return bootstrapped
