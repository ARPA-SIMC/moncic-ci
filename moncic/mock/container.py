import subprocess
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, override

from moncic.container import Container, MaintenanceContainer, Result
from moncic.container.binds import BindConfig
from moncic.runner import CompletedCallable, RunConfig
from moncic.utils.script import Script

from .image import MockImage


class MockContainer(Container):
    """Mock container used for tests."""

    image: MockImage

    @override
    def host_run(
        self, cmd: list[str], check: bool = True, cwd: Path | None = None, interactive: bool = False
    ) -> subprocess.CompletedProcess[bytes]:
        self.image.session.run_log.append(cmd, {})
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    @override
    @contextmanager
    def _container(self) -> Generator[None, None, None]:
        self.image.session.run_log.append_action(f"{self.image.name}: container start")
        yield None
        self.image.session.run_log.append_action(f"{self.image.name}: container stop")

    @override
    def get_root(self) -> Path:
        raise NotImplementedError()

    @override
    def get_pid(self) -> int:
        raise NotImplementedError()

    @override
    def binds(self) -> Iterator[BindConfig]:
        raise NotImplementedError()

    @override
    def run(self, command: list[str], config: RunConfig | None = None) -> subprocess.CompletedProcess[bytes]:
        self.image.session.run_log.append(command, {})
        # run_config = self.config.run_config(config)
        # self.image.images.session.mock_log(system=self.image.name, action="run", config=run_config, cmd=command)
        # return self.image.images.session.get_process_result(args=command)
        return CompletedCallable(command, 0, b"", b"")

    @override
    def run_script(self, script: str | Script, config: RunConfig | None = None) -> subprocess.CompletedProcess[bytes]:
        self.image.session.run_log.append_script(script)
        return CompletedCallable(["script"], 0, b"", b"")

    @override
    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        raise NotImplementedError()
        # run_config = self.config.run_config(config)
        # self.image.images.session.mock_log(
        #     system=self.image.name,
        #     action="run callable",
        #     config=run_config,
        #     func=func.__name__,
        #     desc=func.__doc__,
        #     args=args,
        #     kwargs=kwargs,
        # )
        # return CompletedCallable(args=func.__name__, returncode=0)


class MockMaintenanceContainer(MockContainer, MaintenanceContainer):
    pass
