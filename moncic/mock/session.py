import re
import shlex
import subprocess
import sys
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, override
from collections.abc import Callable

from moncic.images import BootstrappingImages, Images
from moncic.session import Session
from moncic.utils.deb import DebCache
from moncic.utils.script import Script
from moncic.runner import UserConfig

if TYPE_CHECKING:
    import podman as _podman

    from moncic.moncic import Moncic


class MockRunLog:
    """Collect a trace of all mock operations run."""

    def __init__(self) -> None:
        self.log: list[tuple[str, dict[str, Any]]] = []

    def append_action(self, action: str) -> None:
        self.log.append((action, {}))

    def append(self, cmd: list[str], kwargs: dict[str, Any]) -> None:
        self.log.append((shlex.join(cmd), kwargs))

    def append_script(self, script: Script) -> None:
        self.log.append((script.title, {"script": script}))

    def append_callable(self, func: Callable[[], int | None]) -> None:
        self.log.append((f"callable:{func.__name__}", {}))

    def append_forward_user(self, user: UserConfig) -> None:
        self.log.append((f"forward_user:{user.user_name},{user.user_id},{user.group_name},{user.group_id}", {}))

    def append_cachedir(self) -> None:
        self.log.append(("cachedir_tag:", {}))


class MockSession(Session):
    """
    Mock session used for tests
    """

    def __init__(self, moncic: "Moncic", images_class: type[Images] | None = None) -> None:
        from .images import MockImages

        self.images_class = images_class or MockImages

        super().__init__(moncic)
        self.run_log = MockRunLog()
        self.log: list[dict[str, Any]] = []
        self.process_result_queue: dict[str, subprocess.CompletedProcess] = {}

    @override
    def _instantiate_images_imagedir(self, path: Path) -> None:
        from moncic.nspawn.images import NspawnImages

        from .images import MockImages

        images: Images
        if issubclass(self.images_class, MockImages):
            images = self.images_class(self)
        elif issubclass(self.images_class, NspawnImages):
            images = self.images_class(self, path)
        else:
            raise NotImplementedError()

        self.images.add(images)
        self.bootstrapper = images

    @override
    def _instantiate_images_default(self) -> None:
        from moncic.nspawn.images import NspawnImages

        from .images import MockImages

        images: BootstrappingImages
        if issubclass(self.images_class, MockImages):
            images = self.images_class(self)
        elif issubclass(self.images_class, NspawnImages):
            images = self.images_class.create_machinectl(self)
        else:
            raise NotImplementedError()

        self.images.add(images)
        self.bootstrapper = images

    @override
    @cached_property
    def podman(self) -> "_podman.PodmanClient":
        raise NotImplementedError()

    @override
    @cached_property
    def debcache(self) -> DebCache | None:
        return None

    @override
    @cached_property
    def apt_archives(self) -> Path | None:
        return None

    @override
    @cached_property
    def extra_packages_dir(self) -> Path | None:
        return None

    def mock_log(self, **kwargs: Any) -> None:
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
    ) -> None:
        self.process_result_queue[regex] = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )
