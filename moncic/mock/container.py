import subprocess
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from subprocess import CompletedProcess
from typing import override

from moncic.container import Container, MaintenanceContainer, RunConfig
from moncic.container.binds import BindConfig
from moncic.runner import UserConfig
from moncic.utils.script import Script

from .image import MockRunnableImage


class MockContainer(Container):
    """Mock container used for tests."""

    image: MockRunnableImage

    @override
    def host_run(
        self, cmd: list[str], check: bool = True, cwd: Path | None = None, interactive: bool = False
    ) -> subprocess.CompletedProcess[bytes]:
        self.image.session.run_log.append(cmd, {})
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    @override
    @contextmanager
    def _container(self) -> Generator[None, None, None]:
        with self.image.session.run_log.push(f"{self.image.name}: run container"):
            yield None

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
        return CompletedProcess(command, 0, b"", b"")

    @override
    def run_script(self, script: Script, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        self.image.session.run_log.append_script(script)
        return CompletedProcess(["script"], 0, b"", b"")

    @override
    def forward_user(self, user: UserConfig, allow_maint: bool = False) -> None:
        self.image.session.run_log.append_forward_user(user)

    @override
    def run_shell(self, config: RunConfig | None) -> subprocess.CompletedProcess[bytes]:
        self.image.session.run_log.append_action("run-shell")
        return subprocess.CompletedProcess(["shell"], stdout=b"", stderr=b"", returncode=0)


class MockMaintenanceContainer(MockContainer, MaintenanceContainer):
    pass
