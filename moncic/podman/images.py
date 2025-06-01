import io
import logging
from typing import TYPE_CHECKING, override

from moncic.distro import DistroFamily, Distro
from moncic.image import BootstrappableImage, RunnableImage, Image
from moncic.images import BootstrappingImages
from moncic.utils.osrelease import parse_osrelase_contents

if TYPE_CHECKING:
    import podman
    from moncic.session import Session

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
    def image(self, name: str, variant_of: Image | None = None) -> RunnableImage:
        """
        Return the configuration for the named system
        """
        from .image import PodmanImage

        podman = self.session.podman
        repo, tag = self.podman_name(name)
        full_name = f"{repo}:{tag}"
        if not podman.images.exists(full_name):
            raise KeyError(f"image {name!r} not found")
        podman_image = podman.images.get(full_name)

        bootstrapped_from: BootstrappableImage | None = None
        match variant_of:
            case None:
                distro = self._find_distro(name, podman, podman_image)
            case BootstrappableImage():
                distro = variant_of.distro
                bootstrapped_from = variant_of
            case RunnableImage():
                # Reuse the previous found runnable image
                return variant_of
            case _:
                raise NotImplementedError(f"variant_of has unknown image type {variant_of.__class__.__name__}")

        return PodmanImage(
            images=self, name=name, distro=distro, podman_image=podman_image, bootstrapped_from=bootstrapped_from
        )

    def _find_distro(
        self, name: str, podman: "podman.client.PodmanClient", podman_image: "podman.domain.images.Image"
    ) -> Distro:
        os_release = podman.containers.run(podman_image, ["cat", "/etc/os-release"], remove=True)
        assert isinstance(os_release, bytes)
        with io.StringIO(os_release.decode()) as fd:
            osr = parse_osrelase_contents(fd, f"{name}:/etc/os-release")
        return DistroFamily.from_osrelease(osr, "test")

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
        prefix = self.session.podman_repository + ":"
        images: list[str] = []
        for image in self.session.podman.images.list(name=prefix + "*"):
            for tag in image.tags:
                if not tag.startswith(prefix):
                    continue
                images.append(tag.removeprefix(prefix))
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
