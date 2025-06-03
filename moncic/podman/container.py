import signal
import subprocess
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, override

import podman

from moncic.container import BindConfig, Container, ContainerConfig, MaintenanceContainer
from moncic.runner import RunConfig, UserConfig
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
        run_config = self.config.run_config(config)
        podman_command = ["podman", "exec"]
        if run_config.interactive:
            podman_command += ["--interactive", "--tty"]
        if run_config.cwd:
            podman_command += ["--workdir", run_config.cwd.as_posix()]
        if run_config.user:
            podman_command += ["--user", run_config.user.user_name]
        if not run_config.use_path:
            podman_command += ["--env", "PATH=/dev/null"]
        # TODO: script.disable_network is ignored on podman
        # TODO: is there a way to make it work?

        podman_command.append(self.container.id)
        podman_command += command
        if run_config.interactive:
            res = subprocess.run(podman_command, check=run_config.check)
        else:
            res = self.host_run(podman_command, check=run_config.check)
        return res

    @override
    def run_script(self, script: Script, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        with self.script_in_guest(script) as guest_path:
            run_config = self.config.run_config()
            run_config.check = check
            run_config.use_path = True
            if script.root:
                run_config.user = UserConfig.root()
            if script.cwd is not None:
                run_config.cwd = script.cwd
            if script.disable_network:
                run_config.disable_network = True

            self.image.logger.info("Running script %s", script.title)
            cmd = [guest_path.as_posix()]
            return self.run(cmd, run_config)


class PodmanMaintenanceContainer(PodmanContainer, MaintenanceContainer):
    """Non-ephemeral container."""
