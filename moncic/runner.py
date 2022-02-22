"""
Infrastructure for running commands (in local or running systems) and logging
their output
"""
from __future__ import annotations
import asyncio
import dataclasses
import grp
import importlib
import logging
import os
import pwd
import shlex
import subprocess
import traceback
from typing import List, Optional, Union, Callable, TYPE_CHECKING

from . import setns
if TYPE_CHECKING:
    from .system import System
    from .container import NspawnContainer


@dataclasses.dataclass
class RunConfig:
    """
    Configuration needed to customize running actions in a container
    """
    # Set to True to raise CalledProcessError if the process exits with a
    # non-zero exit status
    check: bool = True

    # Run in this working directory. Defaults to ContainerConfig.workdir, if
    # set
    cwd: Optional[str] = None

    # Run as the given user (id or name)
    user: Union[None, int, str] = None

    # Run as the given group (id or name)
    group: Union[None, int, str] = None

    # Set to true to connect to the running terminal instead of logging output
    interactive: bool = False

    def get_uid(self) -> Optional[int]:
        """
        If user is not None, resolve it to a user ID. Otherwise return None
        """
        if self.user is None:
            return None

        if isinstance(self.user, int):
            return self.user
        elif self.user.isdigit():
            return int(self.user)
        else:
            pw = pwd.getpwnam(self.user)
            return pw.pw_uid

    def get_gid(self) -> Optional[int]:
        """
        If group is not None, resolve it to a group ID. Otherwise return None
        """
        if self.group is None:
            return None

        if isinstance(self.group, int):
            return self.group
        elif self.group.isdigit():
            return int(self.group)
        else:
            gr = grp.getgrnam(self.group)
            return gr.gr_gid


class Runner:
    """
    Run commands in a system
    """
    def __init__(self, system: System, config: RunConfig):
        super().__init__()
        self.system = system
        self.config = config
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
    def __init__(self, system: System, config: RunConfig, cmd: List[str]):
        super().__init__(system, config)
        self.cmd = cmd

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

        if self.config.check and proc.returncode != 0:
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
    async def start_process(self):
        if self.config.user is not None:
            raise NotImplementedError("support for user config in LocalRunner is not yet implemented")
        if self.config.group is not None:
            raise NotImplementedError("support for group config in LocalRunner is not yet implemented")
        if self.config.interactive is not None:
            raise NotImplementedError("support for interactive config in LocalRunner is not yet implemented")

        self.system.log.info("Running %s", " ".join(shlex.quote(c) for c in self.cmd))

        kwargs = {}
        if self.config.cwd is not None:
            kwargs["cwd"] = self.config.cwd

        return await asyncio.create_subprocess_exec(
                self.cmd[0], *self.cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs)


class SetnsCallableRunner(Runner):
    def __init__(
            self, run: NspawnContainer, config: RunConfig, func: Callable[[], Optional[int]]):
        super().__init__(run.system, config)
        self.leader_pid = int(run.properties["Leader"])
        self.func = func

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
        self.system.log.info("Running %s", self.func.__doc__.strip() if self.func.__doc__ else self.func.__name__)

        uid = self.config.get_uid()
        gid = self.config.get_gid()

        catch_output = not self.config.interactive

        if catch_output:
            # Create pipes for catching stdout and stderr
            stdout_r, stdout_w = os.pipe2(os.O_CLOEXEC)
            stderr_r, stderr_w = os.pipe2(os.O_CLOEXEC)

        pid = os.fork()
        if pid == 0:
            try:
                # TODO: setenv HOME
                if catch_output:
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
                if self.config.cwd is not None:
                    os.chdir(self.config.cwd)
                if gid is not None:
                    os.setresgid(gid, gid, gid)
                if uid is not None:
                    os.setresuid(uid, uid, uid)
                res = self.func()
                os._exit(res if res is not None else 0)
            except Exception:
                traceback.print_exc()
                os._exit(1)
        else:
            if catch_output:
                os.close(stdout_w)
                os.close(stderr_w)

        stdout: Optional[bytes]
        stderr: Optional[bytes]
        if catch_output:
            asyncio.run(self.collect_output(stdout_r, stderr_r))

            stdout = b"".join(self.stdout)
            stderr = b"".join(self.stderr)
        else:
            stdout = None
            stderr = None

        wres = os.waitid(os.P_PID, pid, os.WEXITED)
        if self.config.check and wres.si_status != 0:
            raise subprocess.CalledProcessError(
                    wres.si_status, self.func.__name__, stdout, stderr)

        return subprocess.CompletedProcess(
                self.func.__name__, wres.si_status, stdout, stderr)
