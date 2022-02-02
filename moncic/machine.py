from __future__ import annotations
from typing import List, Optional, Dict, Any, Callable, Tuple
import subprocess
import tempfile
from importlib import reload
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
    Start, stop, and drive a running CI container
    """
    def __init__(self):
        self.started = False

    def start(self) -> None:
        raise NotImplementedError(f"{self.__class__}.start() not implemented")

    def run(self, command: List[str]) -> Dict[str, Any]:
        """
        Run the given command inside the machine.

        Returns a dict with:
        {
            "stdout": bytes,
            "stderr": bytes,
            "returncode": int,
        }

        stdout and stderr are logged in real time as the process is running.
        """
        raise NotImplementedError(f"{self.__class__}.run(...) not implemented")

    def run_shell(self):
        self.run(["/bin/bash", "-"])

    def run_callable(self, func: Callable[[], Optional[int]]) -> int:
        """
        Run the given callable in a separate process inside the running
        machine. Returns the process exit status.
        """
        raise NotImplementedError(f"{self.__class__}.run_callable(...) not implemented")

    def terminate(self) -> None:
        raise NotImplementedError(f"{self.__class__}.terminate(...) not implemented")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.terminate()
        return self.workdir.__exit__(exc_type, exc_value, exc_tb)


class MockMachine(Machine):
    """
    Mock machine that just logs what is run and does nothing, useful for tests
    """
    def __init__(self):
        super().__init__()
        self.run_log: List[Tuple[str, str]] = []

    def start(self):
        if self.started:
            return
        self.run_log = []
        self.started = True

    def terminate(self):
        self.started = False

    def run(self, command: List[str]) -> Dict[str, Any]:
        self.run_log.append(("command", " ".join(shlex.quote(c) for c in command)))
        return {
            "stdout": b'',
            "stderr": b'',
            "returncode": 0,
        }

    def run_callable(self, func: Callable[[], Optional[int]]) -> int:
        self.run_log.append(("callable", func.__name__))
        return 0


class NspawnMachine(Machine):
    """
    Manage a CI machine
    """
    def __init__(self, ostree: str, name: Optional[str] = None, ephemeral: bool = True):
        """
        Manage a machine where to run CI scripts.

        ``name`` is the name of the machine instance, as available in ``machinectl``
        ``ostree`` is the path to the btrfs subtree with the OS filesystem
        """
        super().__init__()
        if name is None:
            name = str(uuid.uuid4())
        self.machine_name = name
        self.ostree = os.path.abspath(ostree)
        self.ephemeral = ephemeral
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
        runner = SystemdRunRunner(self.machine_name, command)
        return runner.run()

    def run_callable(self, func: Callable[[], Optional[int]]) -> int:
        pid = os.fork()
        if pid == 0:
            logging.shutdown()
            reload(logging)
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
        return super().__enter__()


class LegacyNspawnMachine(NspawnMachine):
    """
    Version of Machine that runs an old OS that it is not compatibile with
    ``systemd-run --wait`` (for example, Centos7)
    """

    def run(self, command: List[str]) -> Dict[str, Any]:
        runner = LegacyRunner(self.machine_name, command)
        return runner.run()
