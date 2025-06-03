import signal
import subprocess
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, override

import podman

from moncic.container import BindConfig, Container, ContainerConfig, MaintenanceContainer, RunConfig
from moncic.runner import UserConfig
from moncic.utils.script import Script

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
                "Target": self.guest_scriptdir.as_posix(),
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

    @override
    def run(self, command: list[str], config: RunConfig | None = None) -> subprocess.CompletedProcess[bytes]:
        assert self.container is not None

        if config is None:
            config = RunConfig()
        if config.cwd is None:
            config.cwd = self.config.get_default_cwd()
        if config.user is None:
            config.user = self.config.get_default_user()

        podman_command = ["podman", "exec"]
        if config.interactive:
            podman_command += ["--interactive", "--tty"]
        if config.cwd:
            podman_command += ["--workdir", config.cwd.as_posix()]
        if config.user:
            podman_command += ["--user", config.user.user_name]
        # TODO: script.disable_network is ignored on podman
        # TODO: is there a way to make it work?

        # if home_bind:
        #     return home_bind.destination
        # elif res.user is not None and res.user.user_id != 0:
        #     return Path(f"/home/{res.user.user_name}")
        # else:
        #     return Path("/root")

        podman_command.append(self.container.id)
        podman_command += command
        if config.interactive:
            res = subprocess.run(podman_command, check=config.check)
        else:
            res = self.host_run(podman_command, check=config.check)
        return res

    @override
    def run_script(self, script: Script, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        with self.script_in_guest(script) as guest_path:
            config = RunConfig()
            config.check = check
            if script.root:
                config.user = UserConfig.root()
            else:
                config.user = self.config.get_default_user()
            if script.cwd is not None:
                config.cwd = script.cwd
            else:
                config.cwd = self.config.get_default_cwd()
            if script.disable_network:
                config.disable_network = True

            self.image.logger.info("Running script %s", script.title)
            cmd = [guest_path.as_posix()]
            return self.run(cmd, config)


class PodmanMaintenanceContainer(PodmanContainer, MaintenanceContainer):
    """Non-ephemeral container."""
