import contextlib
import contextvars
import os
import re
import shlex
import subprocess
import sys
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import context
from .context import privs
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

    def __init__(self, moncic: "Moncic"):
        from .images import BootstrappingImages, ImageRepository

        super().__init__()
        self.moncic = moncic
        self.orig_moncic: contextvars.Token | None = None
        self.orig_session: contextvars.Token | None = None
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
        from .podman.images import PodmanImages

        podman_images = PodmanImages(self)
        self.images.add(podman_images)

        if privs.can_regain():
            images = self.enter_context(NspawnImageStorage.create(self, MACHINECTL_PATH).images())
            assert isinstance(images, BootstrappingImages)
            self.images.add(images)
            self.bootstrapper = images
        else:
            self.bootstrapper = podman_images

    def __enter__(self):
        self.orig_moncic = context.moncic.set(self.moncic)
        self.orig_session = context.session.set(self)
        return super().__enter__()

    def __exit__(self, *args):
        res = super().__exit__(*args)
        if self.orig_session is not None:
            context.session.reset(self.orig_session)
        if self.orig_moncic is not None:
            context.moncic.set(self.orig_moncic)
        return res

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


class MockSession(Session):
    """
    Mock session used for tests
    """

    def __init__(self, moncic: "Moncic"):
        super().__init__(moncic)
        self.log: list[dict[str, Any]] = []
        self.process_result_queue: dict[str, subprocess.CompletedProcess] = {}

    def mock_log(self, **kwargs: Any):
        caller_stack = sys._getframe(1)
        kwargs.setdefault("func", caller_stack.f_code.co_name)
        self.log.append(kwargs)

    def get_process_result(self, *, args: list[str]) -> subprocess.CompletedProcess:
        cmdline = shlex.join(args)
        for regex, result in self.process_result_queue.items():
            if re.search(regex, cmdline):
                self.process_result_queue.pop(regex)
                result.args = args
                return result
        return subprocess.CompletedProcess(args=args, returncode=0)

    def set_process_result(
        self,
        regex: str,
        *,
        returncode: int = 0,
        stdout: str | bytes | None = None,
        stderr: str | bytes | None = None,
    ):
        self.process_result_queue[regex] = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @cached_property
    def debcache(self) -> DebCache | None:
        """
        Return the DebCache object to manage an apt package cache
        """
        return None

    @cached_property
    def apt_archives(self) -> Path | None:
        """
        Return the path of a directory that can be bind-mounted as
        /var/cache/apt/archives in Debian containers
        """
        return None

    @cached_property
    def extra_packages_dir(self) -> Path | None:
        """
        Return the path of a directory with extra packages to add as a source
        to containers
        """
        return None
