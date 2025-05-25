import contextlib
import contextvars
import importlib.util
import os
import types
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Self, override

from . import context
from .context import privs
from .exceptions import Fail
from .utils.deb import DebCache
from .utils.fs import extra_packages_dir

if TYPE_CHECKING:
    import podman as _podman

    from .moncic import Moncic

MACHINECTL_PATH = Path("/var/lib/machines")


class Session(contextlib.ExitStack):
    """
    Hold shared resourcse during a single-threaded Moncic-CI work session
    """

    def __init__(self, moncic: "Moncic") -> None:
        from .images import BootstrappingImages, ImageRepository

        super().__init__()
        self.moncic = moncic
        self.orig_moncic: contextvars.Token["Moncic"] | None = None
        self.orig_session: contextvars.Token["Session"] | None = None
        #: Prefix used to filter podman repositories that Moncic-CI will use
        self.podman_repository_prefix = "localhost/moncic-ci/"

        #: Images used to bootstrap OS images
        self.bootstrapper: BootstrappingImages

        # Container images
        self.images = ImageRepository(self)
        if self.moncic.config.imagedir is None:
            self._instantiate_images_default()
        else:
            self._instantiate_images_imagedir(self.moncic.config.imagedir)

    def _instantiate_images_imagedir(self, path: Path) -> None:
        from .images import BootstrappingImages
        from .nspawn.imagestorage import NspawnImageStorage

        imagestorage = NspawnImageStorage.create(self, path)
        images = self.enter_context(imagestorage.images())
        assert isinstance(images, BootstrappingImages)
        self.images.add(images)
        self.bootstrapper = images

    def _instantiate_images_default(self) -> None:
        from .images import BootstrappingImages
        from .nspawn.imagestorage import NspawnImageStorage

        podman_images: BootstrappingImages | None = None
        if importlib.util.find_spec("podman"):
            from .podman.images import PodmanImages

            podman_images = PodmanImages(self)
            self.images.add(podman_images)

        if privs.can_regain():
            images = self.enter_context(NspawnImageStorage.create(self, MACHINECTL_PATH).images())
            assert isinstance(images, BootstrappingImages)
            self.images.add(images)
            self.bootstrapper = images
        elif podman_images:
            self.bootstrapper = podman_images
        else:
            raise Fail("neither nspawn nor podman are accessible (try running with sudo?)")

    @override
    def __enter__(self) -> Self:
        self.orig_moncic = context.moncic.set(self.moncic)
        self.orig_session = context.session.set(self)
        return super().__enter__()

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        super().__exit__(exc_type, exc_val, exc_tb)
        if self.orig_session is not None:
            context.session.reset(self.orig_session)
        if self.orig_moncic is not None:
            context.moncic.reset(self.orig_moncic)

    @cached_property
    def podman(self) -> "_podman.PodmanClient":
        import podman

        uri = f"unix:///run/user/{os.getuid()}/podman/podman.sock"
        return self.enter_context(podman.PodmanClient(base_url=uri))

    @cached_property
    def debcache(self) -> DebCache | None:
        """
        Return the DebCache object to manage an apt package cache
        """
        if path := self.moncic.config.deb_cache_dir:
            return self.enter_context(DebCache(path))
        else:
            return None

    @cached_property
    def apt_archives(self) -> Path | None:
        """
        Return the path of a directory that can be bind-mounted as
        /var/cache/apt/archives in Debian containers
        """
        if debcache := self.debcache:
            return self.enter_context(debcache.apt_archives())
        else:
            return None

    @cached_property
    def extra_packages_dir(self) -> Path | None:
        """
        Return the path of a directory with extra packages to add as a source
        to containers
        """
        if path := self.moncic.config.extra_packages_dir:
            return self.enter_context(extra_packages_dir(path))
        else:
            return None
