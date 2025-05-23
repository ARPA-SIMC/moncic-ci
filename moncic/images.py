import abc
import logging
import subprocess
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from .image import BootstrappableImage, Image, RunnableImage
    from .session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = "/var/lib/machines"


class ImagesBase(abc.ABC):
    """Base class for images providers."""

    def __init__(self, session: "Session") -> None:
        self.session = session

    @abc.abstractmethod
    def get_logger(self) -> logging.Logger:
        """Create the logger for this Images instance."""

    @cached_property
    def logger(self) -> logging.Logger:
        """Logger for this Images instance."""
        return self.get_logger()

    @abc.abstractmethod
    def list_images(self) -> list[str]:
        """List the names of images found in image directories."""

    @abc.abstractmethod
    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""

    @abc.abstractmethod
    def image(self, name: str) -> "Image":
        """Return the named Image."""

    @abc.abstractmethod
    def deduplicate(self) -> None:
        """Deduplicate storage of common files (if supported)."""

    def host_run(
        self, cmd: list[str], check: bool = True, cwd: Path | None = None, interactive: bool = False
    ) -> subprocess.CompletedProcess:
        """Run a command in the host system."""
        from .runner import LocalRunner

        return LocalRunner.run(self.logger, cmd, check=check, cwd=cwd, interactive=interactive)


class Images(ImagesBase, abc.ABC):
    """Manage access to a group of container images."""

    @override
    def deduplicate(self) -> None:
        pass  # do nothing by default


class BootstrappingImages(Images, abc.ABC):
    """Image repository that can bootstrap images."""

    @abc.abstractmethod
    def bootstrap(self, image: "BootstrappableImage") -> "RunnableImage":
        """Bootstrap an image, returning its runnable version."""


class ImageRepository(ImagesBase):
    """Aggregation of multiple Images."""

    def __init__(self, session: "Session") -> None:
        super().__init__(session)
        from .provision.images import ConfiguredImages, DistroImages

        self.distro_images = DistroImages(self.session)
        self.configured_images = ConfiguredImages(self.session)
        self.images: list[Images] = []
        self.images.append(self.distro_images)
        self.images.append(self.configured_images)

    @override
    def get_logger(self) -> logging.Logger:
        return logging.getLogger("images")

    def add(self, images: Images) -> None:
        self.images.append(images)

    @override
    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        return any(i.has_image(name) for i in self.images)

    @override
    def list_images(self) -> list[str]:
        """List the names of images found in image directories."""
        res: set[str] = set()
        for images in self.images:
            res.update(images.list_images())
        return sorted(res)

    def parent_image(self, name: str, parent_of: str) -> "BootstrappableImage":
        """Return the parent image for a named image."""
        # TODO: change this once podman can generate BoostrappableImage images
        if name != parent_of and self.configured_images.has_image(name):
            return self.configured_images.image(name)
        return self.distro_images.image(name)

    @override
    def image(self, name: str) -> "Image":
        """Instantiate an image by name."""
        from .image import BootstrappableImage, RunnableImage

        result: Image | None = None
        for images in self.images:
            if not images.has_image(name):
                continue
            if result is None:
                result = images.image(name)
                continue

            match (image := images.image(name)):
                case BootstrappableImage():
                    match result:
                        case BootstrappableImage():
                            # Replace with a later definition
                            result = image
                        case RunnableImage():
                            raise NotImplementedError()
                        case _:
                            raise NotImplementedError()
                case RunnableImage():
                    match result:
                        case BootstrappableImage():
                            # Replace with the runnable version
                            image.set_bootstrap_from(result)
                            result = image
                        case RunnableImage():
                            # Keep previously found image
                            pass
                        case _:
                            raise NotImplementedError()
                case _:
                    raise NotImplementedError()

        if result is None:
            raise KeyError(f"Image {name!r} not found")

        return result

    @override
    def deduplicate(self) -> None:
        """Deduplicate storage of common files (if supported)."""
        for images in self.images:
            images.deduplicate()
