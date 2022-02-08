"""
Infrastructure for running commands (in local or running systems) and logging
their output
"""
from __future__ import annotations
import asyncio
import importlib
import logging
import os
import shlex
import subprocess
import traceback
from typing import List, Dict, Any, Optional, Callable

from . import setns

log = logging.getLogger(__name__)


class RunFailed(Exception):
    """
    Exception raised when a task failed to run on the machine
    """
    pass


class OutputLogMixin:
    def __init__(self):
        super().__init__()
        self.stdout: List[bytes] = []
        self.stderr: List[bytes] = []

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


class AsyncioRunner(OutputLogMixin):
    def __init__(self, cmd: List[str]):
        super().__init__()
        self.cmd = cmd

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
            raise RunFailed(f"Command exited with code {proc.returncode}")

        return {
            "stdout": b"".join(self.stdout),
            "stderr": b"".join(self.stderr),
            "returncode": proc.returncode,
        }


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
    def __init__(self, machine_name: str, cmd: List[str], **kwargs):
        super().__init__(cmd)
        self.machine_name = machine_name
        self.kwargs = kwargs


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
                stderr=asyncio.subprocess.PIPE,
                **self.kwargs)


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
                stderr=asyncio.subprocess.PIPE,
                **self.kwargs)


class SetnsCallableRunner(OutputLogMixin):
    def __init__(self, leader_pid: int, func: Callable[[], Optional[int]], cwd: Optional[str] = None):
        super().__init__()
        self.leader_pid = leader_pid
        self.func = func
        self.cwd = cwd

    async def make_reader(self, fd: int):
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        reader_protocol = asyncio.StreamReaderProtocol(reader)
        transport, protocol = await loop.connect_read_pipe(
                lambda: reader_protocol, os.fdopen(fd))
        return reader

    async def collect_output(self, stdout_r: int, stderr_r: int):
        # See https://gist.github.com/oconnor663/08c081904264043e55bf
        stdout_reader = await self.make_reader(stdout_r)
        stderr_reader = await self.make_reader(stderr_r)

        await asyncio.gather(
            self.read_stdout(stdout_reader),
            self.read_stderr(stderr_reader),
        )

    def run(self) -> int:
        # Create pipes for catching stdout and stderr
        stdout_r, stdout_w = os.pipe2(os.O_CLOEXEC)
        stderr_r, stderr_w = os.pipe2(os.O_CLOEXEC)

        pid = os.fork()
        if pid == 0:
            try:
                # Close stdin
                os.close(0)
                os.close(stdout_r)
                os.close(stderr_r)
                # Redirect stdout and stderr to the pipes to parent
                os.dup2(stdout_w, 1)
                os.close(stdout_w)
                os.dup2(stderr_w, 2)
                os.close(stderr_w)

                logging.shutdown()
                importlib.reload(logging)
                setns.nsenter(self.leader_pid)
                if self.cwd is not None:
                    os.chdir(self.cwd)
                res = self.func()
                os._exit(res if res is not None else 0)
            except Exception:
                traceback.print_exc()
                os._exit(1)
        else:
            os.close(stdout_w)
            os.close(stderr_w)

        asyncio.run(self.collect_output(stdout_r, stderr_r))

        res = os.waitid(os.P_PID, pid, os.WEXITED)
        return {
            "stdout": b"".join(self.stdout),
            "stderr": b"".join(self.stderr),
            "returncode": res.si_status,
        }
