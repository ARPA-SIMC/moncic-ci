import subprocess
import signal
import os
import warnings
import tempfile
from pathlib import Path
from typing import Any, override
from collections.abc import Callable
from collections.abc import Iterator

import podman

from moncic.container import BindConfig, Container, ContainerConfig, Result
from moncic.runner import CompletedCallable, RunConfig, SetnsCallableRunner, UserConfig
from moncic.context import privs
from moncic.utils.script import Script

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
    def forward_user(self, user: UserConfig, allow_maint=False) -> None:
        raise NotImplementedError()

    @override
    def _start(self) -> None:
        if self.container is not None:
            raise RuntimeError("Container already started")
        # TODO: self.config.ephemeral
        # TODO: self.config.tmpfs
        # TODO: self.config.forward_user: UserConfig | None = None
        mounts: list[dict[str, Any]] = []
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

        # # Read machine properties
        # res = subprocess.run(["machinectl", "show", self.instance_name], capture_output=True, text=True, check=True)
        # self.properties = {}
        # for line in res.stdout.splitlines():
        #     key, value = line.split("=", 1)
        #     self.properties[key] = value

        # # Do user forwarding if requested
        # if self.config.forward_user:
        #     self.forward_user(self.config.forward_user)

        # # We do not need to delete the user if it was created, because we
        # # enforce that forward_user is only used on ephemeral containers

        # # Set up volatile mounts
        # if any(bind.setup for bind in self.active_binds):
        #     raise NotImplementedError()
        #     self.run_callable(self._bind_setup, config=RunConfig(user=UserConfig.root()))

    @override
    def _stop(self, exc: Exception | None = None) -> None:
        if self.container is None:
            return
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
            podman_command += ["--workdir", config.cwd]

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
    def run_script(self, script: str | Script, config: RunConfig | None = None) -> CompletedCallable:
        assert self.container is not None

        if isinstance(script, Script):
            name = script.title
            with tempfile.NamedTemporaryFile("w+t") as tf:
                script.print(file=tf)
                tf.flush()
                os.fchmod(tf.fileno(), 0o700)
                subprocess.run(["podman", "cp", tf.name, f"{self.container.id}:/root/script"], check=True)
        else:
            if len(script) > 200:
                name = f"script: {script[:200]!r}…"
            else:
                name = f"script: {script!r}"

            with tempfile.NamedTemporaryFile("w+t") as tf:
                tf.write(script)
                tf.flush()
                os.fchmod(tf.fileno(), 0o700)
                subprocess.run(["podman", "cp", tf.name, f"{self.container.id}:/root/script"], check=True)

        run_config = self.config.run_config(config)
        res = self._run(["/root/script"], run_config)
        return CompletedCallable(name, res.returncode, res.stdout, res.stderr)

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
