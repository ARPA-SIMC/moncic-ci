from __future__ import annotations
import contextlib
import errno
import logging
import os
import shlex
import signal
import subprocess
import tempfile
import time
from typing import List, Optional, Callable, TYPE_CHECKING
import uuid

from .runner import SetnsCallableRunner
if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)


def escape_bind_ro(s: str):
    r"""
    Escape a path for use in systemd-nspawn --bind-ro.

    Man systemd-nspawn says:

      Backslash escapes are interpreted, so "\:" may be used to embed
      colons in either path.
    """
    return s.replace(":", r"\:")


class RunningSystem(contextlib.ExitStack):
    """
    An instance of a System in execution as a container
    """
    def __init__(self, system: System, instance_name: Optional[str] = None):
        super().__init__()
        self.system = system

        if instance_name is None:
            self.instance_name = str(uuid.uuid4())
        else:
            self.instance_name = instance_name

        self.started = False

    def run(self, command: List[str]) -> subprocess.CompletedProcess:
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
        raise NotImplementedError(f"{self.__class__}.run() not implemented")

    def run_script(self, body: str) -> subprocess.CompletedProcess:
        """
        Run the given string as a script in the machine.

        A shebang at the beginning of the script will be honored.

        Returns the process exit status.
        """
        raise NotImplementedError(f"{self.__class__}.run_script() not implemented")

    def run_callable(self, func: Callable[[], Optional[int]]) -> subprocess.CompletedProcess:
        """
        Run the given callable in a separate process inside the running
        system. Returns the process exit status.
        """
        raise NotImplementedError(f"{self.__class__}.run_callable() not implemented")

    def start(self):
        """
        Start the running system
        """
        raise NotImplementedError(f"{self.__class__}.start() not implemented")

    def terminate(self) -> None:
        """
        Shut down the running system
        """
        raise NotImplementedError(f"{self.__class__}.terminate() not implemented")

    def __enter__(self):
        self.start()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.terminate()
        return super().__exit__(exc_type, exc_value, exc_tb)


class MaintenanceRunningSystem(RunningSystem):
    """
    RunningSystem with maintenance-oriented functions.

    When a container is run using a MaintenanceRunningSystem it is not
    ephemeral, and changes to its filesystem persist after shutdown.
    """
    pass


class NspawnMixin:
    """
    Running system implemented using systemd nspawn
    """
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        # machinectl properties of the running machine
        self.properties = None
        # systemd-nspawn --bind pathspecs to bind read-write
        self.bind: List[str] = []
        # systemd-nspawn --bind_ro pathspecs to bind read-only
        self.bind_ro: List[str] = []
        # Bind mount this directory in the running system and use it as default
        # working directory
        self.workdir: Optional[str] = None

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

        log.info("Running %s", " ".join(shlex.quote(c) for c in systemd_run_cmd))
        res = subprocess.run(systemd_run_cmd, capture_output=True)
        if res.returncode != 0:
            log.error("Failed to run %s (exit code %d): %r",
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
        if self.workdir is not None:
            self.workdir = os.path.abspath(self.workdir)
            name = os.path.basename(self.workdir)
            if name.startswith("."):
                raise RuntimeError(f"Repository directory name {name!r} cannot start with a dot")
            cmd.append(f"--bind={escape_bind_ro(self.workdir)}:/root/{escape_bind_ro(name)}")
        if self.bind:
            for pathspec in self.bind:
                cmd.append("--bind=" + pathspec)
        if self.bind_ro:
            for pathspec in self.bind_ro:
                cmd.append("--bind-ro=" + pathspec)
        return cmd

    def get_shell_start_command(self):
        cmd = ["systemd-nspawn", "-D", self.system.path]
        if self.workdir is not None:
            self.workdir = os.path.abspath(self.workdir)
            name = os.path.basename(self.workdir)
            if name.startswith("."):
                raise RuntimeError(f"Repository directory name {name!r} cannot start with a dot")
            cmd.append(f"--bind={escape_bind_ro(self.workdir)}:/root/{escape_bind_ro(name)}")
            cmd.append(f"--chdir=/root/{name}")
        if self.bind:
            for pathspec in self.bind:
                cmd.append("--bind=" + pathspec)
        if self.bind_ro:
            for pathspec in self.bind_ro:
                cmd.append("--bind-ro=" + pathspec)
        return cmd

    def start(self):
        if self.started:
            return

        log.info("Starting system %s as %s using image %s", self.system.name, self.instance_name, self.system.path)

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

    def run(self, command: List[str], **kwargs) -> subprocess.CompletedProcess:
        if self.workdir is not None:
            name = os.path.basename(self.workdir)
            kwargs.setdefault("cwd", f"/root/{name}")
        runner = self.system.distro.runner_class(self, command, **kwargs)
        return runner.execute()

    def run_script(self, body: str, **kwargs) -> subprocess.CompletedProcess:
        chroot = self.properties["RootDirectory"]
        with tempfile.TemporaryDirectory(dir=os.path.join(chroot, "root")) as workdir:
            with open(os.path.join(workdir, "script"), "wt") as fd:
                fd.write(body)
                fd.flush()
                os.chmod(fd.fileno(), 0o700)
            inside_workdir = os.path.join("/root", os.path.basename(workdir))
            kwargs.setdefault("cwd", inside_workdir)
            return self.run([os.path.join(inside_workdir, "script")], **kwargs)

    def run_callable(self, func: Callable[[], Optional[int]], **kwargs) -> subprocess.CompletedProcess:
        if self.workdir is not None:
            name = os.path.basename(self.workdir)
            kwargs.setdefault("cwd", f"/root/{name}")

        runner = SetnsCallableRunner(int(self.properties["Leader"]), func, **kwargs)
        return runner.execute()

    def shell(self):
        """
        Open a shell on the given ostree
        """
        cmd = self.get_shell_start_command()
        log.info("Running %s", ' '.join(shlex.quote(c) for c in cmd))
        subprocess.run(cmd)

    def terminate(self):
        if not self.started:
            return

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

        # res = subprocess.run(["machinectl", "stop", self.instance_name])
        # if res.returncode != 0:
        #     raise RuntimeError(f"Terminating machine {self.instance_name} failed with code {res.returncode}")
        self.started = False


class EphemeralNspawnRunningSystem(NspawnMixin, RunningSystem):
    def get_start_command(self):
        cmd = super().get_start_command()
        cmd.append("--ephemeral")
        return cmd

    def get_shell_start_command(self):
        cmd = super().get_shell_start_command()
        cmd.append("--ephemeral")
        return cmd


class MaintenanceNspawnRunningSystem(NspawnMixin, MaintenanceRunningSystem):
    pass
