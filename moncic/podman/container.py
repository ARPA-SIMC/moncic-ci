import subprocess
import signal
import warnings
from pathlib import Path
from contextlib import contextmanager
from typing import Any, override, Generator
from collections.abc import Callable
from collections.abc import Iterator

import podman

from moncic.container import BindConfig, Container, ContainerConfig, Result
from moncic.runner import CompletedCallable, RunConfig, SetnsCallableRunner, UserConfig
from moncic.context import privs

from .image import PodmanImage


class PodmanContainer(Container):
    """
    Running system implemented using systemd nspawn
    """

    image: PodmanImage

    def __init__(
        self, image: PodmanImage, *, config: ContainerConfig | None = None, instance_name: str | None = None
    ) -> None:
        config = self._container_config(image, config)
        super().__init__(image, config=config, instance_name=instance_name)
        self.container: podman.domain.containers.Container | None = None

    @classmethod
    def _container_config(cls, image: PodmanImage, config: ContainerConfig | None = None) -> ContainerConfig:
        """
        Create or complete a ContainerConfig
        """
        if config is None:
            config = ContainerConfig()

        # Allow distro-specific setup
        image.distro.container_config_hook(image, config)

        # Force ephemeral to True in plain systems
        config.ephemeral = True

        return config

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
        # TODO: self.config.forward_user: UserConfig | None = None
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
            # TODO: group_add
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
            # TODO: user
        }

        self.image.logger.debug("Starting container %r", container_kwargs)

        self.container = self.image.images.session.podman.containers.create(
            self.image.podman_image, ["sleep", "inf"], **container_kwargs
        )
        self.container.start()
        self.container.wait(condition="running")

        # def _create(self, command: list[str], config: RunConfig) -> podman.domain.containers.Container:
        #     podman_container = self.image.images.session.podman.containers.create(
        #         self.image.podman_image,
        #         command,
        #         auto_remove=True,
        #         detach=False,
        #         # TODO: environment
        #         # TODO: group_add
        #         # TODO: what is isolation?
        #         # TODO: mounts
        #         # TODO: name
        #         # TODO: privileged
        #         # TODO: read_only
        #         # TODO: read_write_tmpfs
        #         remove=True,
        #         stdout=False,
        #         stderr=False,
        #         # TODO: stream
        #         # TODO: ulimits
        #         # TODO: user
        #         working_dir=config.cwd,
        #     )
        #     # TODO: handle run_config: user
        #     # if run_config.user:
        #     #     podman_command += ["--user", run_config.user.user_name]
        #     # TODO: handle run_config: use_path (not supported)
        #     return podman_container

        try:
            yield None
        finally:
            self.container.reload()
            self.container.kill(signal.SIGKILL)
            self.container.wait(condition="stopped")
            self.container = None

    def _run(self, command: list[str], config: RunConfig) -> subprocess.CompletedProcess:
        assert self.container is not None
        kwargs: dict[str, Any] = {}
        podman_command = ["podman", "exec"]
        if config.interactive:
            podman_command += ["--interactive", "--tty"]
        else:
            kwargs["capture_output"] = True
        if config.cwd:
            podman_command += ["--workdir", config.cwd.as_posix()]
        if config.user:
            podman_command += ["--user", config.user.user_name]

        podman_command.append(self.container.id)
        podman_command += command
        res = subprocess.run(podman_command, check=False, **kwargs)
        if config.check and res.returncode != 0:
            self.image.logger.error("Script failed with return code %d", res.returncode)
            for line in res.stdout.decode().splitlines():
                self.image.logger.error("Script stdout: %s", line)
            for line in res.stderr.decode().splitlines():
                self.image.logger.error("Script stderr: %s", line)
            res.check_returncode()
        return res

    @override
    def run(self, command: list[str], config: RunConfig | None = None) -> CompletedCallable:
        assert self.container is not None
        run_config = self.config.run_config(config)
        res = self._run(command, run_config)
        return CompletedCallable(command, res.returncode, res.stdout, res.stderr)

    @override
    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        warnings.warn("please migrate away from run_callable which requires root", DeprecationWarning)
        run_config = self.config.run_config(config)
        runner = SetnsCallableRunner(self, run_config, func, args, kwargs)
        with privs.root():
            return runner.execute()


class PodmanMaintenanceContainer(PodmanContainer):
    """Non-ephemeral container."""

    @classmethod
    def _container_config(cls, image: PodmanImage, config: ContainerConfig | None = None) -> ContainerConfig:
        config = super()._container_config(image, config)
        # Force ephemeral to False in maintenance systems
        config.ephemeral = False
        return config
