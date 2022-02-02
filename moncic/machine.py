from __future__ import annotations
from typing import List, Optional, Dict, Any, Callable
import subprocess
import tempfile
import logging
import shlex
import uuid
import os
from .runner import SystemdRunRunner, LegacyRunner
from . import setns

log = logging.getLogger(__name__)

# Based on https://github.com/Truelite/nspawn-runner


class Machine:
    """
    Manage a CI machine
    """
    def __init__(self, ostree: str, name: Optional[str] = None, ephemeral: bool = True):
        """
        Manage a machine where to run CI scripts.

        ``name`` is the name of the machine instance, as available in ``machinectl``
        ``ostree`` is the path to the btrfs subtree with the OS filesystem
        """
        if name is None:
            name = str(uuid.uuid4())
        self.machine_name = name
        self.ostree = os.path.abspath(ostree)
        self.ephemeral = ephemeral
        self.started = False
        # machinectl properties of the running machine
        self.properties = None
        self.workdir = tempfile.TemporaryDirectory()

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

    def start(self):
        if self.started:
            return

        log.info("Starting machine using image %s", self.ostree)

        cmd = [
            "systemd-nspawn",
            "--quiet",
            f"--directory={self.ostree}",
            f"--machine={self.machine_name}",
            "--boot",
            "--notify-ready=yes",
            f"--bind={self.workdir.name}:/root/transfer",
        ]
        if self.ephemeral:
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
        """
        Run the given command inside the machine
        """
        runner = SystemdRunRunner(self.machine_name, command)
        return runner.run()

        # log.info("Running %s", " ".join(shlex.quote(c) for c in cmd))
        # res = subprocess.run(cmd, capture_output=True)

        # # TODO: collect stdout/stderr into RunFailed?
        # # TODO: but still let it run on stdout/stderr for progress?
        # if res.returncode != 0:
        #     raise RunFailed(f"Run script exited with code {res.returncode}")

        # return {
        #     "stdout": res.stdout,
        #     "stderr": res.stderr,
        #     "returncode": res.returncode,
        # }

    def run_shell(self):
        self.run(["/bin/bash", "-"])

    def run_callable(self, func: Callable[[], Optional[int]]) -> int:
        """
        Run the given callable in a separate process inside the running
        machine. Returns the process exit status.
        """
        pid = os.fork()
        if pid == 0:
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

    def __enter__(self):
        self.workdir.__enter__()
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.terminate()
        return self.workdir.__exit__(exc_type, exc_value, exc_tb)


class LegacyMachine(Machine):
    """
    Version of Machine that runs an old OS that it is not compatibile with
    ``systemd-run --wait`` (for example, Centos7)
    """

    def run(self, command: List[str]) -> Dict[str, Any]:
        """
        Run the given script inside the machine, using the given shell
        """
        runner = LegacyRunner(self.machine_name, command)
        return runner.run()
