from __future__ import annotations
import contextlib
import importlib
import logging
import os
import shlex
import subprocess
import tempfile
from typing import List, Optional, Dict, Any, Callable, TYPE_CHECKING
import uuid

from . import setns
if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)


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

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.terminate()
        return super().__exit__(exc_type, exc_value, exc_tb)


class NspawnRunningSystem(RunningSystem):
    """
    Running system implemented using systemd nspawn
    """
    def __init__(self, system: System, instance_name: Optional[str] = None):
        super().__init__(system, instance_name)
        # machinectl properties of the running machine
        self.properties = None
        self.workdir = self.enter_context(tempfile.TemporaryDirectory())

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
        return [
            "systemd-nspawn",
            "--quiet",
            f"--directory={self.system.path}",
            f"--machine={self.instance_name}",
            "--boot",
            "--notify-ready=yes",
            f"--bind={self.workdir}:/root/transfer",
        ]

    def start(self):
        if self.started:
            return

        log.info("Starting system %s as %s using image %s", self.system.name, self.instance_name, self.system.root)

        cmd = self.get_start_command()

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
        runner = self.system.distro.runner_class(self.instance_name, command)
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
        if not self.started:
            return

        res = subprocess.run(["machinectl", "terminate", self.machine_name])
        if res.returncode != 0:
            raise RuntimeError(f"Terminating machine {self.machine_name} failed with code {res.returncode}")
        self.started = False


class UpdateMixin:
    def update(self):
        """
        Run periodic maintenance on the system
        """
        for cmd in self.system.distro.get_update_script():
            self.run(cmd)


class EphemeralNspawnRunningSystem(NspawnRunningSystem):
    def get_start_command(self):
        cmd = super().get_start_command()
        cmd.append("--ephemeral")
        return cmd


class MaintenanceNspawnRunningSystem(UpdateMixin, NspawnRunningSystem):
    pass
