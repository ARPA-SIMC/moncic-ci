import logging
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

    def podman_name(self, name: str) -> tuple[str, str]:
        """Return a podman (repo, tag) for a Moncic-CI image name."""
        if ":" in name:
            name = "_".join(name.split(":"))
        return self.session.podman_repository, name

    @override
    def get_logger(self) -> logging.Logger:
        return logging.getLogger("images.podman")

    @override
    def image(self, name: str) -> "PodmanImage":
        """
        Return the configuration for the named system
        """
        from .image import PodmanImage

        repo, tag = self.podman_name(name)
        full_name = f"{repo}:{tag}"
        podman = self.session.podman
        if podman.images.exists(full_name):
            podman_image = podman.images.get(full_name)
            return PodmanImage(images=self, name=name, podman_image=podman_image)
        else:
            raise KeyError(f"image {name!r} not found")

    @override
    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        repo, tag = self.podman_name(name)
        return self.session.podman.images.exists(repo + tag)

    @override
    def list_images(self) -> list[str]:
        """
        List the names of images found in image directories
        """
        images: list[str] = []
        for image in self.session.podman.images.list(name=self.session.podman_repository + "*"):
            for tag in image.tags:
                if not tag.startswith(self.session.podman_repository):
                    continue
                images.append(tag.removeprefix(self.session.podman_repository))
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
        dest_repository, dest_tag = self.podman_name(image.name)
        podman_image.tag(dest_repository, dest_tag)

        res = self.image(image.name)
        res.update()
        return res
