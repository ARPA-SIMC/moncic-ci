import re
import shlex
import subprocess
import sys
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from moncic.session import Session
from moncic.utils.deb import DebCache
from moncic.images import Images

if TYPE_CHECKING:
    import podman as _podman

    from moncic.moncic import Moncic
    from moncic.unittest import MockRunLog


class MockSession(Session):
    """
    Mock session used for tests
    """

    def __init__(self, moncic: "Moncic", run_log: "MockRunLog", images_class: type[Images] | None = None) -> None:
        from .images import MockImages

        self.images_class = images_class or MockImages

        super().__init__(moncic)
        self.run_log = run_log
        self.log: list[dict[str, Any]] = []
        self.process_result_queue: dict[str, subprocess.CompletedProcess] = {}

    @override
    def _instantiate_images_imagedir(self, path: Path) -> None:
        from .images import MockImages
        from moncic.nspawn.images import NspawnImages

        images: Images
        if issubclass(self.images_class, MockImages):
            images = self.images_class(self)
        elif issubclass(self.images_class, NspawnImages):
            images = self.images_class(self, path)
        else:
            raise NotImplementedError()

        self.images.add(images)
        self.bootstrapper = images

    def _instantiate_images_default(self) -> None:
        from .images import MockImages
        from moncic.nspawn.images import NspawnImages

        if issubclass(self.images_class, MockImages):
            images = self.images_class(self)
        elif issubclass(self.images_class, NspawnImages):
            images = self.images_class.create_machinectl()
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
