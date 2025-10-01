import re
import shlex
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, override
from collections.abc import Callable, Generator

from moncic.images import Images
from moncic.session import Session
from moncic.utils.deb import DebCache
from moncic.utils.script import Script
from moncic.runner import UserConfig
from moncic.moncic import Moncic, MoncicConfig

if TYPE_CHECKING:
    import podman as _podman


class MockRunLog:
    """Collect a trace of all mock operations run."""

    def __init__(self) -> None:
        self.log: list[tuple[str, dict[str, Any]]] = []
        self.paused = False

    def append_action(self, action: str) -> None:
        if self.paused:
            return
        self.log.append((action, {}))

    def append(self, cmd: list[str], kwargs: dict[str, Any]) -> None:
        if self.paused:
            return
        self.log.append((shlex.join(cmd), kwargs))

    def append_script(self, script: Script) -> None:
        if self.paused:
            return
        self.log.append((script.title, {"script": script}))

    def append_callable(self, func: Callable[[], int | None]) -> None:
        if self.paused:
            return
        self.log.append((f"callable:{func.__name__}", {}))

    def append_forward_user(self, user: UserConfig) -> None:
        if self.paused:
            return
        self.log.append((f"forward_user:{user.user_name},{user.user_id},{user.group_name},{user.group_id}", {}))

    def append_cachedir(self) -> None:
        if self.paused:
            return
        self.log.append(("cachedir_tag:", {}))

    @contextmanager
    def pause(self) -> Generator[None, None, None]:
        """Pause logging for the duration of this context manager."""
        old_paused = self.paused
        self.paused = True
        try:
            yield
        finally:
            self.paused = old_paused


class MockSession(Session):
    """
    Mock session used for tests
    """

    def __init__(self, moncic: "Moncic", bootstrapper_cls: type[Images] | None = None) -> None:
        super().__init__(moncic)
        if bootstrapper_cls is None:
            from .images import MockImages

            bootstrapper_cls = MockImages

        self.run_log = MockRunLog()
        self.bootstrapper = bootstrapper_cls(self)
        self.images.add(self.bootstrapper)

        self.log: list[dict[str, Any]] = []
        self.process_result_queue: dict[str, subprocess.CompletedProcess] = {}

    @override
    def _make_podman(self) -> "_podman.PodmanClient":
        raise NotImplementedError()

    @override
    def _make_debcache(self, path: Path) -> DebCache:
        raise NotImplementedError()

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


class MockMoncic(Moncic):
    """Mock Moncic that instantiates MockSessions."""

    def __init__(self, config: "MoncicConfig"):
        super().__init__(config)
        self.last_run_log: MockRunLog | None = None

    @override
    def session(self) -> Session:
        res = MockSession(self)
        self.last_run_log = res.run_log
        return res
