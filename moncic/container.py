from __future__ import annotations

import dataclasses
import errno
import grp
import logging
import os
import pwd
import shlex
import signal
import subprocess
import tempfile
import time
import uuid
from typing import (TYPE_CHECKING, Callable, ContextManager, List, NoReturn,
                    Optional, Protocol)

from .nspawn import escape_bind_ro
from .runner import RunConfig, SetnsCallableRunner, UserConfig

if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)


@dataclasses.dataclass
class ContainerConfig:
    """
    Configuration needed to customize starting a container
    """
    # If true, changes done to the container filesystem will not persist
    ephemeral: bool = True

    # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
    #
    # Leave to None to use system or container defaults.
    tmpfs: Optional[bool] = None

    # Bind mount this directory in the running system and use it as default
    # working directory
    workdir: Optional[str] = None

    # systemd-nspawn --bind pathspecs to bind read-write
    bind: List[str] = dataclasses.field(default_factory=list)

    # systemd-nspawn --bind_ro pathspecs to bind read-only
    bind_ro: List[str] = dataclasses.field(default_factory=list)

    # If set to True: if workdir is None, make sure the current user exists in
    # the container. Else, make sure the owner of workdir exists in the
    # container. Cannot be used when ephemeral is False
    forward_user: bool = False

    def check(self):
        """
        Raise exceptions if options are used inconsistently
        """
        pass

    def run_config(self, run_config: Optional[RunConfig] = None) -> RunConfig:
        if run_config is None:
            res = RunConfig()
        else:
            res = run_config

        if res.cwd is None:
            if self.workdir is not None:
                name = os.path.basename(self.workdir)
                res.cwd = f"/tmp/{name}"
            elif res.user is not None and res.user.user_id != 0:
                res.cwd = f"/home/{res.user.user_name}"
            else:
                res.cwd = "/root"

        if self.workdir is not None and res.user is None:
            res.user = UserConfig.from_file(self.workdir)

        return res


class Container(ContextManager, Protocol):
    """
    An instance of a System in execution as a container
    """
    system: System
    config: ContainerConfig

    def forward_user(self, user: UserConfig, allow_maint: bool = False):
        """
        Ensure the system has a matching user and group
        """
        ...

    def run(self, command: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        """
        Run the given command inside the running system.

        Returns a dict with:
        {
            "stdout": bytes,
            "stderr": bytes,
            "returncode": int,
        }

        stdout and stderr are logged in real time as the process is running.
        """
        ...

    def run_script(self, body: str, config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        """
        Run the given string as a script in the machine.

        A shebang at the beginning of the script will be honored.

        Returns the process exit status.
        """
        ...

    def run_callable(
            self, func: Callable[[], Optional[int]], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        """
        Run the given callable in a separate process inside the running
        system. Returns the process exit status.
        """
        ...


class ContainerBase:
    """
    Convenience common base implementation for Container
    """
    def __init__(self, system: System, config: ContainerConfig, instance_name: Optional[str] = None):
        super().__init__()
        self.system = system

        if instance_name is None:
            self.instance_name = str(uuid.uuid4())
        else:
            self.instance_name = instance_name

        config.check()
        self.config = config
        self.started = False

    def _start(self):
        raise NotImplementedError(f"{self.__class__}._start not implemented")

    def _stop(self) -> ContextManager:
        raise NotImplementedError(f"{self.__class__}._stop not implemented")

    def __enter__(self):
        if not self.started:
            try:
                self._start()
            except Exception:
                if self.started:
                    self._stop()
                raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.started:
            self._stop()


class NspawnContainer(ContainerBase):
    """
    Running system implemented using systemd nspawn
    """
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        # machinectl properties of the running machine
        self.properties = None

    def _run_nspawn(self, cmd: List[str]):
        """
        Run the given systemd-nspawn command line, contained into its own unit
        using systemd-run
        """
        unit_config = [
            'KillMode=mixed',
            'Type=notify',
            'RestartForceExitStatus=133',
            'SuccessExitStatus=133',
            'Slice=machine.slice',
            'Delegate=yes',
            'TasksMax=16384',
            'WatchdogSec=3min',
        ]

        systemd_run_cmd = ["systemd-run"]
        for c in unit_config:
            systemd_run_cmd.append(f"--property={c}")

        systemd_run_cmd.extend(cmd)

        self.system.log.info("Running %s", " ".join(shlex.quote(c) for c in systemd_run_cmd))
        res = subprocess.run(systemd_run_cmd, capture_output=True)
        if res.returncode != 0:
            self.system.log.error("Failed to run %s (exit code %d): %r",
                                  " ".join(shlex.quote(c) for c in systemd_run_cmd),
                                  res.returncode,
                                  res.stderr)
            raise RuntimeError("Failed to start container")

    def get_start_command(self):
        cmd = [
            "systemd-nspawn",
            "--quiet",
            f"--directory={self.system.path}",
            f"--machine={self.instance_name}",
            "--boot",
            "--notify-ready=yes",
        ]
        if self.config.workdir is not None:
            workdir = os.path.abspath(self.config.workdir)
            name = os.path.basename(workdir)
            if name.startswith("."):
                raise RuntimeError(f"Repository directory name {name!r} cannot start with a dot")
            cmd.append(f"--bind={escape_bind_ro(workdir)}:/tmp/{escape_bind_ro(name)}")
        if self.config.bind:
            for pathspec in self.config.bind:
                cmd.append("--bind=" + pathspec)
        if self.config.bind_ro:
            for pathspec in self.config.bind_ro:
                cmd.append("--bind-ro=" + pathspec)
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
        if self.system.images.moncic.systemd_version >= 250:
            cmd.append("--suppress-sync=yes")
        return cmd

    def forward_user(self, user: UserConfig, allow_maint=False):
        """
        Ensure the system has a matching user and group
        """
        def forward():
            try:
                pw = pwd.getpwuid(user.user_id)
            except KeyError:
                pw = None
                if not allow_maint and not self.config.ephemeral:
                    raise RuntimeError(f"user {user.user_name} not found in non-ephemeral containers")

            try:
                gr = grp.getgrgid(user.group_id)
            except KeyError:
                gr = None
                if not allow_maint and not self.config.ephemeral:
                    raise RuntimeError(f"user group {user.group_name} not found in non-ephemeral containers")

            if pw is None and gr is None:
                subprocess.run([
                    "groupadd",
                    "--gid", str(user.group_id),
                    user.group_name], check=True)
                subprocess.run([
                    "useradd",
                    "--create-home",
                    "--uid", str(user.user_id),
                    "--gid", str(user.group_id),
                    user.user_name], check=True)
            else:
                user.check_system()
        forward.__doc__ = f"check or create user {user.user_name!r} and group {user.group_name!r}"

        self.run_callable(forward, config=RunConfig(user=UserConfig.root()))

    def _start(self):
        self.system.log.info("Starting system %s as %s using image %s",
                             self.system.name, self.instance_name, self.system.path)

        cmd = self.get_start_command()

        self._run_nspawn(cmd)
        self.started = True

        # Read machine properties
        res = subprocess.run(
                ["machinectl", "show", self.instance_name],
                capture_output=True, text=True, check=True)
        self.properties = {}
        for line in res.stdout.splitlines():
            key, value = line.split('=', 1)
            self.properties[key] = value

        # Do user forwarding if requested
        if self.config.forward_user:
            if self.config.workdir is None:
                user = UserConfig.from_sudoer()
            else:
                user = UserConfig.from_file(self.config.workdir)
            self.forward_user(user)

        # We do not need to delete the user if it was created, because we
        # enforce that forward_user is only used on ephemeral containers

    def _stop(self):
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

    def run(self, command: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        run_config = self.config.run_config(config)

        exec_func: Callable[[str, List[str]], NoReturn]
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

        return self.run_callable(command_runner, run_config)

    def run_script(self, body: str, config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
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

        return self.run_callable(script_runner, config)

    def run_callable(
            self, func: Callable[[], Optional[int]], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        run_config = self.config.run_config(config)
        runner = SetnsCallableRunner(self, run_config, func)
        return runner.execute()
