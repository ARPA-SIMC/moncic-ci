import signal
import subprocess
import warnings
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, override

import podman

from moncic.container import BindConfig, Container, ContainerConfig, MaintenanceContainer, Result
from moncic.context import privs
from moncic.runner import CompletedCallable, RunConfig, SetnsCallableRunner

from .image import PodmanImage


class PodmanContainer(Container):
    """
    Running system implemented using systemd nspawn
    """

    image: PodmanImage

    def __init__(self, image: PodmanImage, *, config: ContainerConfig, instance_name: str | None = None) -> None:
        super().__init__(image, config=config, instance_name=instance_name)
        self.container: podman.domain.containers.Container | None = None

    @override
    def get_root(self) -> Path:
        raise NotImplementedError()

    @override
    def get_pid(self) -> int:
        assert self.container is not None
        info = self.container.inspect()
        pidfile = Path(info["PidFile"])
        return int(pidfile.read_text())

    @override
    def binds(self) -> Iterator[BindConfig]:
        raise NotImplementedError()

    @override
    @contextmanager
    def _container(self) -> Generator[None, None, None]:
        if self.container is not None:
            raise RuntimeError("Container already started")
        # TODO: self.config.ephemeral
        # TODO: self.config.tmpfs
        mounts: list[dict[str, Any]] = [
            {
                "Type": "bind",
                "Readonly": "true",
                "Source": self.scriptdir.as_posix(),
                "Target": self.mounted_scriptdir.as_posix(),
            }
        ]
        for bind in self.config.binds:
            mounts.append(bind.to_podman())

        container_kwargs: dict[str, Any] = {
            "auto_remove": True,
            "detach": True,
            # TODO: environment
            # TODO: what is isolation?
            "mounts": mounts,
            # TODO: name
            # TODO: privileged
            # TODO: read_only
            # TODO: read_write_tmpfs
            "remove": True,
            "stdout": False,
            "stderr": False,
            # TODO: stream
            # TODO: ulimits
        }

        self.image.logger.debug("Starting container %r", container_kwargs)

        self.container = self.image.session.podman.containers.create(
            self.image.podman_image, ["sleep", "inf"], **container_kwargs
        )
        self.container.start()
        self.container.wait(condition="running")

        try:
            yield None
        finally:
            self.container.reload()
            if not self.ephemeral:
                self.image.commit(self)
            self.container.kill(signal.SIGKILL)
            self.container.wait(condition="stopped")
            self.container = None

    def _run(self, command: list[str], config: RunConfig) -> subprocess.CompletedProcess[bytes]:
        assert self.container is not None
        podman_command = ["podman", "exec"]
        if config.interactive:
            podman_command += ["--interactive", "--tty"]
        if config.cwd:
            podman_command += ["--workdir", config.cwd.as_posix()]
        if config.user:
            podman_command += ["--user", config.user.user_name]

        podman_command.append(self.container.id)
        podman_command += command
        if config.interactive:
            res = subprocess.run(podman_command, check=config.check)
        else:
            res = self.host_run(podman_command, check=config.check)
        return res

    @override
    def run(self, command: list[str], config: RunConfig | None = None) -> subprocess.CompletedProcess[bytes]:
        assert self.container is not None
        run_config = self.config.run_config(config)
        return self._run(command, run_config)

    @override
    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        warnings.warn("please migrate away from run_callable which requires root", DeprecationWarning)
        run_config = self.config.run_config(config)
        runner = SetnsCallableRunner(self, run_config, func, args, kwargs)
        with privs.root():
            return runner.execute()


class PodmanMaintenanceContainer(PodmanContainer, MaintenanceContainer):
    """Non-ephemeral container."""
