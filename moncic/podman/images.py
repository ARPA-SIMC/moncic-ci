import logging
from functools import cached_property
from typing import TYPE_CHECKING, override
from pathlib import Path

from moncic.image import Image
from moncic.images import Images

if TYPE_CHECKING:
    import podman

    from moncic.session import Session

log = logging.getLogger("images")


class PodmanImages(Images):
    """Access podman images."""

    def __init__(self, session: "Session") -> None:
        self.session = session
        self.repository_prefix = "localhost/moncic-ci/"

    @override
    def image(self, name: str) -> Image:
        """
        Return the configuration for the named system
        """
        from .image import PodmanImage, PodmanUnbootstrappedImage

        full_name = self.repository_prefix + name
        podman = self.session.podman
        if podman.images.exists(full_name):
            podman_image = podman.images.get(full_name)
            return PodmanImage(images=self, name=name, podman_image=podman_image)
        elif path := self._configured_images.get(name):
            return PodmanUnbootstrappedImage(images=self, name=name, config_path=path)
        else:
            raise KeyError(f"image {name!r} not found")

    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        return self.session.podman.images.exists(name)

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

    def list_images(self) -> list[Image]:
        """
        List the names of images found in image directories
        """
        from .image import PodmanImage, PodmanUnbootstrappedImage

        bootstrapped: dict[str, "podman.domain.images.Image"] = {}
        for image in self.session.podman.images.list(name=self.repository_prefix + "*"):
            for tag in image.tags:
                if not tag.startswith(self.repository_prefix):
                    continue
                bootstrapped[tag.removeprefix(self.repository_prefix)] = image

        images: list[Image] = []
        for name in sorted(self._configured_images.keys() | bootstrapped.keys()):
            if img := bootstrapped.get(name):
                images.append(PodmanImage(images=self, name=name, podman_image=img))
            else:
                images.append(
                    PodmanUnbootstrappedImage(images=self, name=name, config_path=self._configured_images[name])
                )
        return images

    def deduplicate(self):
        pass
