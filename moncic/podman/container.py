import logging
import subprocess
import time
from pathlib import Path
from typing import Any, override
from collections.abc import Callable
from collections.abc import Iterator

from moncic.container import BindConfig, Container, ContainerConfig, Result
from moncic.runner import CompletedCallable, RunConfig, SetnsCallableRunner, UserConfig

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
    def binds(self) -> Iterator[BindConfig]:
        raise NotImplementedError()

    @override
    def forward_user(self, user: UserConfig, allow_maint=False) -> None:
        raise NotImplementedError()

    @override
    def _start(self):
        # self.container.start()
        # self.container.wait(condition="running")
        self.started = True

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
    def _stop(self, exc: Exception | None = None):
        # self.container.stop()
        # self.container.wait(condition="stopped")
        self.started = False

    @override
    def run(self, command: list[str], config: RunConfig | None = None) -> CompletedCallable:
        assert self.started
        run_config = self.config.run_config(config)
        podman_command = ["podman", "run", "--rm"]
        kwargs: dict[str, Any] = {}
        if not run_config.interactive:
            kwargs["text"] = True
            kwargs["capture_output"] = True
        if run_config.check:
            kwargs["check"] = True

        # TODO: handle run_config: user
        if run_config.user:
            podman_command += ["--user", run_config.user.user_name]
        # TODO: handle run_config: cwd
        # TODO: handle run_config: use_path

        podman_command += [self.image.id]
        podman_command += command
        res = subprocess.run(podman_command, **kwargs)
        return CompletedCallable(command, res.returncode, res.stdout, res.stderr)

    @override
    def run_script(self, body: str, config: RunConfig | None = None) -> CompletedCallable:
        raise NotImplementedError()

    @override
    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        raise NotImplementedError()


class PodmanMaintenanceContainer(PodmanContainer):
    """Non-ephemeral container."""

    @classmethod
    def _container_config(cls, image: PodmanImage, config: ContainerConfig | None = None) -> ContainerConfig:
        config = super()._container_config(image, config)
        # Force ephemeral to False in maintenance systems
        config.ephemeral = False
        return config
