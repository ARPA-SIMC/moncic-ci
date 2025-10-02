import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, override

from moncic.image import BootstrappableImage, RunnableImage
from moncic.images import Images
from moncic.moncic import Moncic, MoncicConfig
from moncic.provision.config import write_yaml
from moncic.runner import UserConfig
from moncic.session import Session
from moncic.utils.deb import DebCache
from moncic.utils.script import Script

if TYPE_CHECKING:
    import podman as _podman


class RunLogEntry:
    """
    Entry in a mock run log.

    This represents an operation performed by the mock system.

    Entries can be arranged in a tree of sub-entries, to capture the
    lower-level operations done while performing a higher level one.
    """

    def __init__(self, name: str = "root", data: dict[str, Any] | None = None) -> None:
        self.name = name
        self.data = data or {}
        self.entries: list["RunLogEntry"] = []
        self.paused = False

    @override
    def __str__(self) -> str:
        return self.name

    @override
    def __repr__(self) -> str:
        return f"RunLogEntry({self.name}, {self.data})"

    def append_action(self, action: str, data: dict[str, Any] | None = None) -> None:
        if self.paused:
            return
        self.entries.append(RunLogEntry(action, data))

    def append(self, cmd: list[str], kwargs: dict[str, Any]) -> None:
        if self.paused:
            return
        self.entries.append(RunLogEntry(shlex.join(cmd), kwargs))

    def append_script(self, script: Script) -> None:
        if self.paused:
            return
        self.entries.append(RunLogEntry(script.title, {"script": script}))

    def append_callable(self, func: Callable[[], int | None]) -> None:
        if self.paused:
            return
        self.entries.append(RunLogEntry(f"callable:{func.__name__}"))

    def append_forward_user(self, user: UserConfig) -> None:
        if self.paused:
            return
        self.entries.append(RunLogEntry("forward_user", user._asdict()))

    def append_cachedir(self) -> None:
        if self.paused:
            return
        self.entries.append(RunLogEntry("cachedir_tag"))

    def dump(self, file: IO[str] | None = None, *, indent: int = 0) -> None:
        """Print the collected log."""
        for idx, entry in enumerate(self.entries):
            print(f"{indent*' '}{idx:02d}: {entry.name} {entry.data}")
            if entry.entries:
                entry.dump(file, indent=indent + 1)

    @contextmanager
    def pause(self) -> Generator[None, None, None]:
        """Pause logging for the duration of this context manager."""
        old_paused = self.paused
        self.paused = True
        try:
            yield
        finally:
            self.paused = old_paused


class MockRunLog:
    """Collect a trace of all mock operations run."""

    def __init__(self) -> None:
        self.current = RunLogEntry("root")

    def append_action(self, action: str) -> None:
        self.current.append_action(action)

    def append(self, cmd: list[str], kwargs: dict[str, Any]) -> None:
        self.current.append(cmd, kwargs)

    def append_script(self, script: Script) -> None:
        self.current.append_script(script)

    def append_callable(self, func: Callable[[], int | None]) -> None:
        self.current.append_callable(func)

    def append_forward_user(self, user: UserConfig) -> None:
        self.current.append_forward_user(user)

    def append_cachedir(self) -> None:
        self.current.append_cachedir()

    def dump(self, file: IO[str] | None = None) -> None:
        """Print the collected log."""
        self.current.dump(file)

    @contextmanager
    def pause(self) -> Generator[None, None, None]:
        """Pause logging for the duration of this context manager."""
        with self.current.pause():
            yield

    @contextmanager
    def push(self, action: str, data: dict[str, Any] | None = None) -> Generator[None, None, None]:
        """Add an entry and append logs to it until context ends."""
        if self.current.paused:
            yield
            return

        orig = self.current
        self.current.append_action(action, data)
        self.current = self.current.entries[-1]
        try:
            yield
        finally:
            self.current = orig


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

    def test_simulate_bootstrap(self, name: str, config: dict[str, Any] | None = None) -> RunnableImage:
        """Bootstrap an image, optionally writing its config file."""
        if config is not None:
            self.test_write_config(name, config)
        with self.run_log.pause():
            image = self.images.image(name)
            if isinstance(image, RunnableImage):
                return image
            assert isinstance(image, BootstrappableImage)
            return image.bootstrap()

    def test_write_config(self, name: str, config: dict[str, Any]) -> None:
        """Write a configuration file for the named image."""
        confdir = self.moncic.config.imageconfdirs[0]
        with (confdir / f"{name}.yaml").open("w") as out:
            write_yaml(config, out)


class MockMoncic(Moncic):
    """Mock Moncic that instantiates MockSessions."""

    def __init__(self, config: "MoncicConfig"):
        super().__init__(config)
        self.last_session: MockSession | None = None
        self.last_run_log: MockRunLog | None = None

    @override
    def session(self) -> Session:
        self.last_session = MockSession(self)
        self.last_run_log = self.last_session.run_log
        return self.last_session
