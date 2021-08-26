from __future__ import annotations
from typing import List, Optional, Dict, Any
import subprocess
import tempfile
import logging
import shlex
import time
import uuid
import os

log = logging.getLogger(__name__)

# Based on https://github.com/Truelite/nspawn-runner


class RunFailed(Exception):
    """
    Exception raised when a task failed to run on the machine
    """
    pass


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
        subprocess.run(systemd_run_cmd)

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
        ]
        if self.ephemeral:
            cmd.append("--ephemeral")

        self._run_nspawn(cmd)
        self.started = True

    def run(self, command: List[str]) -> Dict[str, Any]:
        """
        Run the given script inside the machine, using the given shell
        """
        cmd = [
            "systemd-run", "--quiet", "--pipe", "--wait",
            f"--machine={self.machine_name}", "--",
        ]
        cmd += command

        log.info("Running %s", " ".join(shlex.quote(c) for c in cmd))
        res = subprocess.run(cmd, capture_output=True)

        # TODO: collect stdout/stderr into RunFailed?
        # TODO: but still let it run on stdout/stderr for progress?
        if res.returncode != 0:
            raise RunFailed(f"Run script exited with code {res.returncode}")

        return {
            "stdout": res.stdout,
            "stderr": res.stderr,
            "returncode": res.returncode,
        }

    def run_shell(self):
        self.run(["/bin/bash", "-"])

    def terminate(self):
        res = subprocess.run(["machinectl", "terminate", self.machine_name])
        if res.returncode != 0:
            raise RuntimeError(f"Terminating machine {self.machine_name} failed with code {res.returncode}")
        self.started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.terminate()


class LegacyPoller:
    def __init__(self, machine_name: str):
        self.machine_name = machine_name
        self.workdir = None

    def __enter__(self):
        self.workdir = tempfile.TemporaryDirectory()
        self.workdir.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        return self.workdir.__exit__(exc_type, exc_value, exc_tb)

    def has_moncic_file(self, name: str) -> bool:
        """
        Check if /root/moncic-{name} exists in the machine
        """
        local_file = os.path.join(self.workdir.name, name)
        try:
            os.remove(local_file)
        except FileNotFoundError:
            pass

        cmd = ["machinectl", "copy-from", self.machine_name, f"/root/moncic-{name}", local_file]

        res = subprocess.run(cmd, capture_output=True, check=False)
        if res.returncode != 0:
            if b"Failed to copy: No such file or directory" in res.stderr:
                return False
            else:
                raise RuntimeError(
                        f"Command {' '.join(shlex.quote(c) for c in cmd)} failed with code {res.returncode}."
                        f" Stderr: {res.stderr!r}")

        os.remove(local_file)
        return True

    def read_moncic_file(self, name: str, required=True) -> Optional[bytes]:
        """
        Return the contents of /root/moncic-{name} in the machine, or None if it does not exist
        """
        local_file = os.path.join(self.workdir.name, name)
        try:
            os.remove(local_file)
        except FileNotFoundError:
            pass

        cmd = ["machinectl", "copy-from", self.machine_name, f"/root/moncic-{name}", local_file]

        res = subprocess.run(cmd, capture_output=True, check=False)
        if res.returncode != 0:
            if not required and res.stderr == "Failed to copy: No such file or directory":
                return None
            else:
                raise RuntimeError(
                        f"Command {' '.join(shlex.quote(c) for c in cmd)} failed with code {res.returncode}."
                        f" Stderr: {res.stderr!r}")

        with open(local_file, "rb") as fd:
            return fd.read()


class LegacyMachine(Machine):
    """
    Version of Machine that runs an old OS that it is not compatibile with
    ``systemd-run --wait`` (for example, Centos7)
    """

    def run(self, command: List[str]) -> Dict[str, Any]:
        """
        Run the given script inside the machine, using the given shell
        """
        script = [
            "#!/bin/sh",
            'cleanup() {'
            '   rm -f "$0"',
            '}',
            "trap cleanup EXIT",
            " ".join(shlex.quote(c) for c in command) + " > /root/moncic-stdout 2> /root/moncic-stderr",
            "echo $? > /root/moncic-retcode",
        ]

        with tempfile.NamedTemporaryFile("wt") as tf:
            for line in script:
                print(line, file=tf)
                tf.flush()

            subprocess.run(["machinectl", "copy-to", self.machine_name, tf.name, "/root/moncic-script"], check=True)

        cmd = [
            "systemd-run", "--quiet",
            f"--machine={self.machine_name}", "--", "/bin/sh", "/root/moncic-script"
        ]

        log.info("Running %s", " ".join(shlex.quote(c) for c in cmd))
        res = subprocess.run(cmd, check=True)

        result = self.poll_command_results()
        retcode = result["returncode"]

        # TODO: collect stdout/stderr into RunFailed?
        # TODO: but still let it run on stdout/stderr for progress?
        if retcode != 0:
            raise RunFailed(f"Run script exited with code {res.returncode}")

        return result

    def run_shell(self):
        raise NotImplementedError(f"running a shell on {self.ostree} is not yet implemented")

    def poll_command_results(self) -> Dict[str, Any]:
        with LegacyPoller(self.machine_name) as poller:
            while True:
                if poller.has_moncic_file("script"):
                    time.sleep(0.2)
                    continue

                return {
                    "returncode": int(poller.read_moncic_file("retcode")),
                    "stdout": poller.read_moncic_file("stdout"),
                    "stderr": poller.read_moncic_file("stderr"),
                }
