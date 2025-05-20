import dataclasses
import errno
import logging
import os
import shlex
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, NoReturn, override

from moncic.container import Container, BindConfig, Result, ContainerConfig
from moncic.runner import CompletedCallable, RunConfig, SetnsCallableRunner, UserConfig

from .image import NspawnImage


class NspawnContainer(Container):
    """
    Running system implemented using systemd nspawn
    """

    image: NspawnImage

    def __init__(
        self, image: NspawnImage, path: Path, *, config: ContainerConfig | None = None, instance_name: str | None = None
    ) -> None:
        config = self._container_config(image, config)
        super().__init__(image, config=config, instance_name=instance_name)
        self.path = path
        # machinectl properties of the running machine
        self.properties: dict[str, str] = {}
        # Bind mounts used by this container
        self.active_binds: list[BindConfig] = []

    @classmethod
    def _container_config(cls, image: NspawnImage, config: ContainerConfig | None = None) -> ContainerConfig:
        """
        Create or complete a ContainerConfig
        """
        if config is None:
            config = ContainerConfig()
            if image.container_info.tmpfs is not None:
                config.tmpfs = image.container_info.tmpfs
            else:
                config.tmpfs = image.images.session.moncic.config.tmpfs
        elif config.ephemeral and config.tmpfs is None:
            # Make a copy to prevent changing the caller's config
            config = dataclasses.replace(config)
            if image.container_info.tmpfs is not None:
                config.tmpfs = image.container_info.tmpfs
            else:
                config.tmpfs = image.images.session.moncic.config.tmpfs

        # Allow distro-specific setup
        image.distro.container_config_hook(image, config)

        # Force ephemeral to True in plain systems
        config.ephemeral = True

        return config

    @override
    def get_root(self) -> Path:
        return Path(self.properties["RootDirectory"])

    def binds(self) -> Iterator[BindConfig]:
        yield from self.active_binds

    def _run_nspawn(self, cmd: list[str]):
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

        self.image.logger.info("Running %s", " ".join(shlex.quote(c) for c in systemd_run_cmd))
        res = subprocess.run(systemd_run_cmd, capture_output=True)
        if res.returncode != 0:
            self.image.logger.error(
                "Failed to run %s (exit code %d): %r",
                " ".join(shlex.quote(c) for c in systemd_run_cmd),
                res.returncode,
                res.stderr,
            )
            raise RuntimeError("Failed to start container")

    def get_start_command(self):
        cmd = [
            "systemd-nspawn",
            "--quiet",
            f"--directory={self.path}",
            f"--machine={self.instance_name}",
            "--boot",
            "--notify-ready=yes",
            "--resolv-conf=replace-host",
        ]
        for bind_config in self.config.binds:
            self.active_binds.append(bind_config)
            cmd.append(bind_config.to_nspawn())
        if self.config.ephemeral:
            if self.config.tmpfs:
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
        return cmd

    @override
    def forward_user(self, user: UserConfig, allow_maint=False) -> None:
        """
        Ensure the system has a matching user and group
        """

        def forward():
            res = subprocess.run(["id", "-u", str(user.user_id)], capture_output=True, check=False)
            has_user = res.returncode == 0 and int(res.stdout.strip()) == user.user_id
            if not has_user and not allow_maint and not self.config.ephemeral:
                raise RuntimeError(f"user {user.user_name} not found in non-ephemeral containers")

            res = subprocess.run(["id", "-g", str(user.user_id)], capture_output=True, check=False)
            has_group = res.returncode == 0 and int(res.stdout.strip()) == user.group_id
            if not has_group and not allow_maint and not self.config.ephemeral:
                raise RuntimeError(f"user group {user.group_name} not found in non-ephemeral containers")

            if not has_user and not has_group:
                subprocess.run(["groupadd", "--gid", str(user.group_id), user.group_name], check=True)
                subprocess.run(
                    [
                        "useradd",
                        "--create-home",
                        "--uid",
                        str(user.user_id),
                        "--gid",
                        str(user.group_id),
                        user.user_name,
                    ],
                    check=True,
                )
            else:
                user.check_system()

        forward.__doc__ = f"check or create user {user.user_name!r} and group {user.group_name!r}"

        self.run_callable(forward, config=RunConfig(user=UserConfig.root()))

    @override
    def _start(self):
        self.image.logger.info(
            "Starting system %s as %s using image %s", self.image.name, self.instance_name, self.image.path
        )

        cmd = self.get_start_command()

        self._run_nspawn(cmd)
        self.started = True

        # Read machine properties
        res = subprocess.run(["machinectl", "show", self.instance_name], capture_output=True, text=True, check=True)
        self.properties = {}
        for line in res.stdout.splitlines():
            key, value = line.split("=", 1)
            self.properties[key] = value

        # Do user forwarding if requested
        if self.config.forward_user:
            self.forward_user(self.config.forward_user)

        # We do not need to delete the user if it was created, because we
        # enforce that forward_user is only used on ephemeral containers

        # Set up volatile mounts
        if any(bind.setup for bind in self.active_binds):
            self.run_callable(self._bind_setup, config=RunConfig(user=UserConfig.root()))

    def _bind_setup(self):
        """
        Run setup scripts from binds
        """
        for bind in self.active_binds:
            if bind.setup:
                bind.setup(bind)

    def _bind_teardown(self):
        """
        Run teardown scripts from binds
        """
        for bind in self.active_binds:
            if bind.teardown:
                bind.teardown(bind)

    @override
    def _stop(self, exc: Exception | None = None):
        # Run teardown script frombinds
        if any(bind.teardown for bind in self.active_binds):
            self.run_callable(self._bind_teardown, config=RunConfig(user=UserConfig.root()))

        # See https://github.com/systemd/systemd/issues/6458
        leader_pid = int(self.properties["Leader"])
        os.kill(leader_pid, signal.SIGRTMIN + 4)
        while True:
            try:
                os.kill(leader_pid, 0)
            except OSError as e:
                if e.errno == errno.ESRCH:
                    break
                raise
            time.sleep(0.1)
        self.started = False

    @override
    def run(self, command: list[str], config: RunConfig | None = None) -> CompletedCallable:
        run_config = self.config.run_config(config)

        exec_func: Callable[[str, list[str]], NoReturn]
        if run_config.use_path:
            exec_func = os.execvp
        else:
            exec_func = os.execv

        def command_runner():
            try:
                exec_func(command[0], command)
            except FileNotFoundError:
                logging.error("%r: command not found", command[0])
                # Same return code as the shell for a command not found
                return 127

        command_runner.__doc__ = " ".join(shlex.quote(c) for c in command)

        return self.run_callable_raw(command_runner, run_config)

    @override
    def run_script(self, body: str, config: RunConfig | None = None) -> CompletedCallable:
        def script_runner():
            with tempfile.TemporaryDirectory() as workdir:
                script_path = os.path.join(workdir, "script")
                with open(script_path, "wt") as fd:
                    fd.write(body)
                    fd.flush()
                    os.chmod(fd.fileno(), 0o700)
                # FIXME: if cwd is set in config, don't chdir here
                #        and don't use a working directory
                os.chdir(workdir)
                os.execv(script_path, [script_path])

        if len(body) > 200:
            script_runner.__doc__ = f"script: {body[:200]!r}…"
        else:
            script_runner.__doc__ = f"script: {body!r}"

        return self.run_callable_raw(script_runner, config)

    @override
    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        run_config = self.config.run_config(config)
        runner = SetnsCallableRunner(self, run_config, func, args, kwargs)
        completed = runner.execute()
        return completed


class NspawnMaintenanceContainer(NspawnContainer):
    """Non-ephemeral container."""

    @classmethod
    def _container_config(cls, image: NspawnImage, config: ContainerConfig | None = None) -> ContainerConfig:
        config = super()._container_config(image, config)
        # Force ephemeral to False in maintenance systems
        config.ephemeral = False
        return config
