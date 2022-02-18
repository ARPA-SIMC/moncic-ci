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
from typing import List, Optional, Callable, TYPE_CHECKING

from . import setns
if TYPE_CHECKING:
    from .system import System
    from .run import NspawnRunningSystem


class Runner:
    """
    Run commands in a system
    """
    def __init__(self, system: System):
        super().__init__()
        self.system = system
        self.stdout: List[bytes] = []
        self.stderr: List[bytes] = []

    async def read_stdout(self, reader: asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stdout.append(line)
            self.system.log.info("stdout: %s", line.decode(errors="replace").rstrip())

    async def read_stderr(self, reader: asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stderr.append(line)
            self.system.log.info("stderr: %s", line.decode(errors="replace").rstrip())


class AsyncioRunner(Runner):
    def __init__(self, system: System, cmd: List[str], check=True):
        super().__init__(system)
        self.cmd = cmd
        self.check = check

    def execute(self):
        return asyncio.run(self._run())

    async def start_process(self):
        """
        Start an asyncio subprocess for the command, returning it so it can be
        supervised
        """
        raise NotImplementedError(f"{self.__class__}.start_process")

    async def _run(self) -> subprocess.CompletedProcess:
        proc = await self.start_process()

        await asyncio.gather(
            self.read_stdout(proc.stdout),
            self.read_stderr(proc.stderr),
            proc.wait(),
        )

        stdout = b"".join(self.stdout)
        stderr = b"".join(self.stderr)

        if self.check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                    proc.returncode,
                    self.cmd,
                    stdout, stderr)

        return subprocess.CompletedProcess(
                self.cmd, proc.returncode, stdout, stderr)


class LocalRunner(AsyncioRunner):
    """
    Run a command locally, logging its output
    """
    def __init__(self, system: System, cmd: List[str], check=True, **kwargs):
        super().__init__(system, cmd, check)
        self.kwargs = kwargs

    async def start_process(self):
        self.system.log.info("Running %s", " ".join(shlex.quote(c) for c in self.cmd))

        return await asyncio.create_subprocess_exec(
                self.cmd[0], *self.cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self.kwargs)


class MachineRunner(AsyncioRunner):
    """
    Base class for running commands in running containers
    """
    def __init__(self, run: NspawnRunningSystem, cmd: List[str], check=True, **kwargs):
        super().__init__(run.system, cmd, check)
        self.run = run
        self.kwargs = kwargs


class SystemdRunRunner(MachineRunner):
    async def start_process(self):
        cmd = [
            "/usr/bin/systemd-run", "--quiet", "--pipe", "--wait",
            "--setenv=HOME=/root",
            f"--machine={self.run.instance_name}",
        ]

        cwd = self.kwargs.get("cwd")
        if cwd is not None:
            cmd.append("--working-directory=" + cwd)
            kwargs = dict(self.kwargs)
            kwargs.pop("cwd")
        else:
            kwargs = self.kwargs

        cmd.append("--")
        cmd += self.cmd

        self.system.log.info("Running %s", " ".join(shlex.quote(c) for c in cmd))

        return await asyncio.create_subprocess_exec(
                cmd[0], *cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs)


class LegacyRunRunner(MachineRunner):
    async def start_process(self):
        # See https://lists.debian.org/debian-devel/2021/12/msg00148.html
        # Thank you Marco d'Itri for the nsenter tip
        leader_pid = int(self.run.properties["Leader"])

        # Verify that we can interact with the given process
        os.kill(leader_pid, 0)

        cmd = ["nsenter", "--mount", "--uts", "--ipc", "--net", "--pid", "--cgroup", "--target", str(leader_pid)]

        cwd = self.kwargs.get("cwd")
        if cwd is not None:
            cmd.append("--wd=" + os.path.join(self.run.properties["RootDirectory"], cwd.lstrip("/")))
            kwargs = dict(self.kwargs)
            kwargs.pop("cwd")
        else:
            kwargs = self.kwargs

        cmd.append("--")
        cmd += self.cmd

        self.system.log.info("Running %s", " ".join(shlex.quote(c) for c in cmd))

        return await asyncio.create_subprocess_exec(
                cmd[0], *cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs)


class SetnsCallableRunner(Runner):
    def __init__(
            self, run: NspawnRunningSystem, func: Callable[[], Optional[int]], cwd: Optional[str] = None, check=True):
        super().__init__(run.system)
        self.leader_pid = int(run.properties["Leader"])
        self.func = func
        self.cwd = cwd
        self.check = check

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

    def execute(self) -> subprocess.CompletedProcess:
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

        stdout = b"".join(self.stdout)
        stderr = b"".join(self.stderr)

        wres = os.waitid(os.P_PID, pid, os.WEXITED)
        if self.check and wres.si_status != 0:
            raise subprocess.CalledProcessError(
                    wres.si_status, self.func.__name__, stdout, stderr)

        return subprocess.CompletedProcess(
                self.func.__name__, wres.si_status, stdout, stderr)
