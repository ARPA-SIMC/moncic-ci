import logging
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, override

from moncic.image import BootstrappableImage, RunnableImage
from moncic.images import BootstrappingImages

if TYPE_CHECKING:
    from moncic.session import Session

    from .image import PodmanImage

log = logging.getLogger("images")


class PodmanImages(BootstrappingImages):
    """Access podman images."""

    def __init__(self, session: "Session") -> None:
        self.session = session

    @override
    def get_logger(self) -> logging.Logger:
        return logging.getLogger("images.podman")

    @override
    def image(self, name: str) -> "PodmanImage":
        """
        Return the configuration for the named system
        """
        from .image import PodmanImage

        full_name = self.session.podman_repository_prefix + name
        podman = self.session.podman
        if podman.images.exists(full_name):
            podman_image = podman.images.get(full_name)
            return PodmanImage(session=self.session, name=name, podman_image=podman_image)
        else:
            raise KeyError(f"image {name!r} not found")

    @override
    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        return self.session.podman.images.exists(self.session.podman_repository_prefix + name)

    @cached_property
    def _configured_images(self) -> dict[str, Path]:
        """Return the list of images defined in configuration files."""
        configured: dict[str, Path] = {}
        for path in self.session.moncic.config.imageconfdirs:
            for f in path.iterdir():
                if f.name.startswith(".") or f.is_dir():
                    continue
                if not f.suffix == ".Containerfile":
                    continue
                configured[f.stem.replace("-", ":")] = f
        return configured

    @override
    def list_images(self) -> list[str]:
        """
        List the names of images found in image directories
        """
        images: list[str] = []
        for image in self.session.podman.images.list(name=self.session.podman_repository_prefix + "*"):
            for tag in image.tags:
                if not tag.startswith(self.session.podman_repository_prefix):
                    continue
                images.append(tag.removeprefix(self.session.podman_repository_prefix))
        images.sort()
        return images

    @override
    def bootstrap(self, image: BootstrappableImage) -> RunnableImage:
        import podman as podman_

        if self.has_image(image.name):
            return self.image(image.name)

        image.logger.info("bootstrapping in podman")

        podman = self.session.podman
        repository, tag = image.distro.get_podman_name()
        image.logger.info("pulling from %s:%s", repository, tag)
        podman_image = podman.images.pull(repository, tag)
        assert isinstance(podman_image, podman_.domain.images.Image)
        podman_image.tag(self.session.podman_repository_prefix + image.name, "latest")

        res = self.image(image.name)
        res.update()
        return res
