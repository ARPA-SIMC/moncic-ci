import errno
import os
import shlex
import signal
import subprocess
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar, override

from moncic import context
from moncic.container import (
    BindConfig,
    Container,
    ContainerCannotStart,
    ContainerConfig,
    MaintenanceContainer,
    RunConfig,
)
from moncic.runner import Runner, UserConfig
from moncic.utils.nspawn import escape_bind_ro
from moncic.utils.script import Script

from .image import NspawnImage

Result = TypeVar("Result")


class NspawnContainer(Container):
    """
    Running system implemented using systemd nspawn
    """

    image: NspawnImage

    def __init__(self, image: NspawnImage, *, config: ContainerConfig, instance_name: str | None = None) -> None:
        super().__init__(image, config=config, instance_name=instance_name)
        # machinectl properties of the running machine
        self.properties: dict[str, str] = {}

    @override
    def get_root(self) -> Path:
        return Path(self.properties["RootDirectory"])

    @override
    def get_pid(self) -> int:
        return int(self.properties["Leader"])

    @override
    def binds(self) -> Iterator[BindConfig]:
        yield from self.config.binds

    def _check_host_system(self) -> None:
        """Check if the container can be started in this host system."""
        # Check if we are trying to run a cgroup v1 guest on a cgroup v2 host
        # See https://github.com/lxc/lxc/issues/4072
        if self.image.distro.cgroup_v1:
            kernel_cmdline = Path("/proc/cmdline").read_text().split()
            if "systemd.unified_cgroup_hierarchy=0" not in kernel_cmdline:
                raise ContainerCannotStart(
                    "Container requires guest cgroup v1, not available on host with cgroup v2."
                    " You can try with the podman backend, or work around it by adding"
                    " 'systemd.unified_cgroup_hierarchy=0` to your host kernel commandline"
                )

    def _run_nspawn(self, cmd: list[str]) -> None:
        """
        Run the given systemd-nspawn command line, contained into its own unit
        using systemd-run
        """
        unit_config = [
            "KillMode=mixed",
            "Type=notify",
            "RestartForceExitStatus=133",
            "SuccessExitStatus=133",
            "Slice=machine.slice",
            "Delegate=yes",
            "TasksMax=16384",
            "WatchdogSec=3min",
        ]

        systemd_run_cmd = ["systemd-run"]
        for c in unit_config:
            systemd_run_cmd.append(f"--property={c}")

        systemd_run_cmd.extend(cmd)

        self.image.logger.info("Running %s", shlex.join(systemd_run_cmd))
        with context.privs.root():
            res = subprocess.run(systemd_run_cmd, capture_output=True)
        if res.returncode != 0:
            self.image.logger.error(
                "Failed to run %s (exit code %d): %r",
                shlex.join(systemd_run_cmd),
                res.returncode,
                res.stderr,
            )
            raise RuntimeError("Failed to start container")

    def get_start_command(self, path: Path) -> list[str]:
        cmd = [
            "systemd-nspawn",
            "--quiet",
            f"--directory={path}",
            f"--machine={self.instance_name}",
            "--boot",
            "--notify-ready=yes",
            "--resolv-conf=replace-host",
            "--timezone=copy",
        ]
        cmd.append(f"--bind-ro={escape_bind_ro(self.scriptdir)}:{escape_bind_ro(self.guest_scriptdir)}")
        for bind_config in self.config.binds:
            cmd.append(bind_config.to_nspawn())
        if self.ephemeral:
            container_info = self.image.get_container_info()
            if container_info.tmpfs is not None:
                tmpfs = container_info.tmpfs
            else:
                tmpfs = self.image.images.session.moncic.config.tmpfs

            if tmpfs:
                cmd.append("--volatile=overlay")
                # See https://github.com/Truelite/nspawn-runner/issues/10
                # According to systemd-nspawn(1), --read-only is implied if --volatile
                # is used, but it seems that without using --read-only one ostree
                # remains locked and VMs can only be started once from it.
                cmd.append("--read-only")
            else:
                cmd.append("--ephemeral")
        if self.image.images.session.moncic.systemd_version >= 250:
            cmd.append("--suppress-sync=yes")
        cmd.append(f"systemd.hostname={self.instance_name}")
        for name in self.image.distro.get_systemd_boot_mask_units():
            cmd.append(f"systemd.mask={name}")
        return cmd

    @override
    @contextmanager
    def _container(self) -> Generator[None, None, None]:
        self._check_host_system()
        with self._container_in_path(self.image.path):
            yield None

    @contextmanager
    def _container_in_path(self, path: Path) -> Generator[None, None, None]:
        self.image.logger.info("Starting system %s as %s using image %s", self.image.name, self.instance_name, path)

        cmd = self.get_start_command(path)
        self._run_nspawn(cmd)

        # Read machine properties
        res = subprocess.run(["machinectl", "show", self.instance_name], capture_output=True, text=True, check=True)
        self.properties = {}
        for line in res.stdout.splitlines():
            key, value = line.split("=", 1)
            self.properties[key] = value

        try:
            yield None
        finally:
            with context.privs.root():
                # See https://github.com/systemd/systemd/issues/6458
                leader_pid = self.get_pid()
                os.kill(leader_pid, signal.SIGRTMIN + 4)
                while True:
                    try:
                        os.kill(leader_pid, 0)
                    except OSError as e:
                        if e.errno == errno.ESRCH:
                            break
                        raise
                    time.sleep(0.1)

    @override
    def run(self, command: list[str], config: RunConfig | None = None) -> subprocess.CompletedProcess[bytes]:
        if config is None:
            config = RunConfig()
        if config.cwd is None:
            config.cwd = self.config.get_default_cwd()
        if config.user is None:
            config.user = self.config.get_default_user()

        systemd_version = self.image.distro.systemd_version

        cmd = [
            "/usr/bin/systemd-run",
            f"--machine={self.instance_name}",
            "--wait",
            "--quiet",
        ]
        if systemd_version is not None and systemd_version > 232:
            cmd.append("--collect")
        if systemd_version is not None and systemd_version > 237:
            cmd.append("--service-type=exec")
        if config.cwd is not None:
            cmd.append(f"--working-directory={config.cwd}")
        if config.interactive:
            cmd.append("--tty")
        else:
            cmd.append("--pipe")
        if config.user is not None:
            cmd += [f"--uid={config.user.user_id}", f"--gid={config.user.group_id}"]
        if config.disable_network:
            # This is ignored, probably because the container has already been started
            cmd += ["--property=PrivateNetwork=true"]
        if not command[0].startswith("/") and systemd_version is not None and systemd_version <= 246:
            command = ["/usr/bin/env"] + command

        # if home_bind:
        #     return home_bind.destination
        # elif res.user is not None and res.user.user_id != 0:
        #     return Path(f"/home/{res.user.user_name}")
        # else:
        #     return Path("/root")

        cmd += command
        with context.privs.root():
            if config.interactive:
                res = subprocess.run(cmd, check=config.check)
            else:
                runner = Runner(self.logger, cmd, check=config.check)
                res = runner.run()

        return res

    @override
    def run_script(self, script: Script, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        with self.script_in_guest(script) as guest_path:
            config = RunConfig()
            config.check = check
            if script.user is not None:
                config.user = script.user
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


class NspawnMaintenanceContainer(NspawnContainer, MaintenanceContainer):
    """Non-ephemeral container."""

    @override
    @contextmanager
    def _container(self) -> Generator[None, None, None]:
        from moncic.provision.image import ConfiguredImage

        self._check_host_system()

        match self.image.bootstrapped_from:
            case ConfiguredImage():
                compression = self.image.bootstrapped_from.config.bootstrap_info.compression
            case _:
                compression = self.image.images.session.moncic.config.compression

        with self.image.images.transactional_workdir(self.image.path, compression) as work_path:
            with self._container_in_path(work_path):
                yield None
