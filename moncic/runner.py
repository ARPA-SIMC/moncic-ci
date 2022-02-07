"""
Infrastructure for running commands (in local or running systems) and logging
their output
"""
from __future__ import annotations
import asyncio
import logging
import os
import shlex
import subprocess
from typing import List, Dict, Any

log = logging.getLogger(__name__)


class RunFailed(Exception):
    """
    Exception raised when a task failed to run on the machine
    """
    pass


class AsyncioRunner:
    def __init__(self, cmd: List[str]):
        self.cmd = cmd
        self.stdout: List[bytes] = []
        self.stderr: List[bytes] = []

    def run(self):
        return asyncio.run(self._run())

    async def start_process(self):
        """
        Start an asyncio subprocess for the command, returning it so it can be
        supervised
        """
        raise NotImplementedError(f"{self.__class__}.start_process")

    async def _run(self) -> Dict[str, Any]:
        proc = await self.start_process()

        await asyncio.gather(
            self.read_stdout(proc.stdout),
            self.read_stderr(proc.stderr),
            proc.wait(),
        )

        if proc.returncode != 0:
            raise RunFailed(f"Run script exited with code {proc.returncode}")

        return {
            "stdout": b"".join(self.stdout),
            "stderr": b"".join(self.stderr),
            "returncode": proc.returncode,
        }

    async def read_stdout(self, reader: asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stdout.append(line)
            log.info("stdout: %s", line.decode(errors="replace").rstrip())

    async def read_stderr(self, reader: asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stderr.append(line)
            log.info("stderr: %s", line.decode(errors="replace").rstrip())


class LocalRunner(AsyncioRunner):
    """
    Run a command locally, logging its output
    """
    def __init__(self, cmd: List[str], **kwargs):
        super().__init__(cmd)
        self.kwargs = kwargs

    async def start_process(self):
        log.info("Running %s", " ".join(shlex.quote(c) for c in self.cmd))

        return await asyncio.create_subprocess_exec(
                self.cmd[0], *self.cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self.kwargs)


class MachineRunner(AsyncioRunner):
    """
    Base class for running commands in running containers
    """
    def __init__(self, machine_name: str, cmd: List[str]):
        super().__init__(cmd)
        self.machine_name = machine_name


class SystemdRunRunner(MachineRunner):
    async def start_process(self):
        cmd = [
            "/usr/bin/systemd-run", "--quiet", "--pipe", "--wait",
            "--setenv=HOME=/root",
            f"--machine={self.machine_name}", "--",
        ]
        cmd += self.cmd

        log.info("Running %s", " ".join(shlex.quote(c) for c in cmd))

        return await asyncio.create_subprocess_exec(
                cmd[0], *cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)


class LegacyRunRunner(MachineRunner):
    async def start_process(self):
        # See https://lists.debian.org/debian-devel/2021/12/msg00148.html
        # Thank you Marco d'Itri for the nsenter tip

        res = subprocess.run(
                ["machinectl", "show", "--property=Leader", "--value", self.machine_name],
                capture_output=True, text=True, check=True)
        leader_pid = int(res.stdout)

        # Verify that we can interact with the given process
        os.kill(leader_pid, 0)

        cmd = ["nsenter", "--mount", "--uts", "--ipc", "--net", "--pid", "--cgroup", "--target", str(leader_pid)]
        cmd += self.cmd

        log.info("Running %s", " ".join(shlex.quote(c) for c in cmd))

        return await asyncio.create_subprocess_exec(
                cmd[0], *cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
