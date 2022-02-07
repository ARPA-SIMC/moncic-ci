from __future__ import annotations
import contextlib
import importlib
import logging
import os
import shlex
import subprocess
from typing import List, Optional, Dict, Any, Callable
import uuid

from .runner import SystemdRunRunner, LocalRunner
from . import setns

log = logging.getLogger(__name__)


class System:
    def __init__(self, name: str, root: str):
        # Name identifying this system
        self.name = name
        # Root path of the ostree of this system
        self.root = root

    def start(self, ephemeral: bool = True, instance_name: Optional[str] = None) -> RunningSystem:
        """
        Boot this system in a container
        """
        raise NotImplementedError(f"{self.__class__}.start() not implemented")

    def bootstrap(self):
        """
        Download or generate the ostree for this system
        """
        with Bootstrapper(self) as bootstrapper:
            # TODO: use distro or yaml config to know what to do
            ...

    def update(self, ostree: str) -> None:
        """
        Run periodic maintenance on the system
        """
        with self.start(instance_name=f"maint-{self.name}", ephemeral=False) as instance:
            # TODO: use distro or yaml config to know what to do
            # TODO: self.run_update(instance)
            ...


class Bootstrapper(contextlib.ExitStack):
    """
    Infrastructure used to bootstrap a System
    """
    def __init__(self, system: System):
        super().__init__()
        self.system = system

    def run(self, cmd: List[str], **kw) -> subprocess.CompletedProcess:
        """
        Wrapper around subprocess.run which logs what is run
        """
        kw.setdefault("cwd", self.system.root)
        runner = LocalRunner(cmd, **kw)
        return runner.run()


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

    def run(self, command: List[str]) -> Dict[str, Any]:
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

    def run_callable(self, func: Callable[[], Optional[int]]) -> int:
        """
        Run the given callable in a separate process inside the running
        system. Returns the process exit status.
        """
        raise NotImplementedError(f"{self.__class__}.run_callable() not implemented")

    def terminate(self) -> None:
        """
        Shut down the running system
        """
        raise NotImplementedError(f"{self.__class__}.terminate() not implemented")

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.terminate()
        return super().__exit__(exc_type, exc_value, exc_tb)


class NspawnRunningSystem(RunningSystem):
    """
    Running system implemented using systemd nspawn
    """
    runner_class = SystemdRunRunner

    def __init__(self, system: System, instance_name: Optional[str] = None):
        super().__init__(system, instance_name)
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

        log.info("Running %s", " ".join(shlex.quote(c) for c in systemd_run_cmd))
        res = subprocess.run(systemd_run_cmd, capture_output=True)
        if res.returncode != 0:
            log.error("Failed to run %s (exit code %d): %r",
                      " ".join(shlex.quote(c) for c in systemd_run_cmd),
                      res.returncode,
                      res.stderr)
            raise RuntimeError("Failed to start container")

    def start(self, ephemeral: bool = True):
        if self.started:
            return

        log.info("Starting system %s as %s using image %s", self.system.name, self.instance_name, self.system.root)

        cmd = [
            "systemd-nspawn",
            "--quiet",
            f"--directory={self.ostree}",
            f"--machine={self.machine_name}",
            "--boot",
            "--notify-ready=yes",
            f"--bind={self.workdir}:/root/transfer",
        ]
        if ephemeral:
            cmd.append("--ephemeral")

        self._run_nspawn(cmd)
        self.started = True

        # Read machine properties
        res = subprocess.run(
                ["machinectl", "show", self.machine_name],
                capture_output=True, text=True, check=True)
        self.properties = {}
        for line in res.stdout.splitlines():
            key, value = line.split('=', 1)
            self.properties[key] = value

    def run(self, command: List[str]) -> Dict[str, Any]:
        runner = self.runner_class(self.instance_name, command)
        return runner.run()

    def run_callable(self, func: Callable[[], Optional[int]]) -> int:
        pid = os.fork()
        if pid == 0:
            logging.shutdown()
            importlib.reload(logging)
            setns.nsenter(int(self.properties["Leader"]))
            res = func()
            if res is None:
                res = 0
            os._exit(res)
        else:
            res = os.waitid(os.P_PID, pid, os.WEXITED)
            return res.si_status

    def terminate(self):
        res = subprocess.run(["machinectl", "terminate", self.machine_name])
        if res.returncode != 0:
            raise RuntimeError(f"Terminating machine {self.machine_name} failed with code {res.returncode}")
        self.started = False
