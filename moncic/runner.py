"""
Infrastructure for running commands (in local or running systems) and logging
their output
"""

import asyncio
import dataclasses
import grp
import logging
import os
import pickle
import pwd
import shlex
import shutil
import struct
import subprocess
import types
from functools import cached_property
from pathlib import Path
from typing import Any, NamedTuple, Self, TypeVar, override

Result = TypeVar("Result")


RESULT_LOG = 0
RESULT_EXCEPTION = 1
RESULT_VALUE = 2


class UserConfig(NamedTuple):
    """
    User and group information to use for running processes
    """

    user_name: str
    user_id: int
    group_name: str
    group_id: int

    @classmethod
    def from_file(cls, pathname: Path) -> Self:
        """
        Instantiate a UserConfig from ownership of a file
        """
        st = pathname.stat()
        pw = pwd.getpwuid(st.st_uid)
        gr = grp.getgrgid(st.st_gid)
        return cls(pw.pw_name, pw.pw_uid, gr.gr_name, gr.gr_gid)

    @classmethod
    def root(cls) -> Self:
        """
        Instantiate a UserConfig for the root user
        """
        return cls("root", 0, "root", 0)

    @classmethod
    def from_current(cls) -> Self:
        """
        Instantiate a UserConfig from the current user and group
        """
        uid = os.getuid()
        if uid == 0:
            return cls.root()
        pw = pwd.getpwuid(uid)
        gr = grp.getgrgid(os.getgid())
        return cls(pw.pw_name, pw.pw_uid, gr.gr_name, gr.gr_gid)

    @classmethod
    def from_sudoer(cls) -> Self:
        """
        Instantiate a UserConfig from the user and group that were active
        before sudo
        """
        if "SUDO_UID" in os.environ:
            uid = int(os.environ["SUDO_UID"])
            gid = int(os.environ["SUDO_GID"])
        else:
            uid = os.getuid()
            gid = os.getgid()
        pw = pwd.getpwuid(uid)
        gr = grp.getgrgid(gid)
        return cls(pw.pw_name, pw.pw_uid, gr.gr_name, gr.gr_gid)

    @classmethod
    def from_user(cls, name: str) -> Self:
        """
        Instantiate a UserConfig from the username of a local user
        """
        pw = pwd.getpwnam(name)
        gr = grp.getgrgid(pw.pw_gid)
        return cls(pw.pw_name, pw.pw_uid, gr.gr_name, gr.gr_gid)

    def check_system(self) -> None:
        """
        Check that this user/group information is consistent in the current
        system
        """
        # Run consistency checks
        if self.user_id == 0 and self.group_id == 0:
            return

        # TODO: do not use pwd and grp, as they may be cached from the host system
        try:
            pw = pwd.getpwuid(self.user_id)
        except KeyError:
            raise RuntimeError(f"container has no user {self.user_id} {self.user_name!r}") from None

        try:
            gr = grp.getgrgid(self.group_id)
        except KeyError:
            raise RuntimeError(f"container has no group {self.group_id} {self.group_name!r}") from None

        if pw.pw_name != self.user_name:
            raise RuntimeError(
                f"user {self.user_id} in container is named {pw.pw_name!r}"
                f" but outside it is named {self.user_name!r}"
            )

        if gr.gr_name != self.group_name:
            raise RuntimeError(
                f"group {self.group_id} in container is named {gr.gr_name!r}"
                f" but outside it is named {self.group_name!r}"
            )


@dataclasses.dataclass
class RunConfig:
    """
    Configuration needed to customize running actions in a container
    """

    # Set to True to raise CalledProcessError if the process exits with a
    # non-zero exit status
    check: bool = True

    # Run in this working directory. Defaults to ContainerConfig.workdir, if
    # set. Else, to the user's home directory
    cwd: Path | None = None

    # Run as the given user. Defaults to the owner of ContainerConfig.workdir,
    # if not set
    user: UserConfig | None = None

    # Set to true to connect to the running terminal instead of logging output
    interactive: bool = False

    # Set to true to lookup the executable in the path instead of assuming it
    # is an absolute path
    use_path: bool = False

    # Run with networking disabled
    disable_network: bool = False


class Runner:
    """
    Run commands in a system
    """

    def __init__(self, logger: logging.Logger, config: RunConfig):
        super().__init__()
        self.log = logger
        self.config = config
        self.stdout: list[bytes] = []
        self.stderr: list[bytes] = []
        self.exc_info: tuple[type[BaseException], BaseException, types.TracebackType] | None = None
        self.has_result: bool = False
        self.result: Any = None

    @cached_property
    def name(self) -> str:
        """
        Return a name describing this runner
        """
        return self._get_name()

    def _get_name(self) -> str:
        """
        Return a name describing this runner
        """
        raise NotImplementedError(f"{self.__class__}._get_name not implemented")

    async def read_stdout(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stdout.append(line)
            self.log.info("stdout: %s", line.decode(errors="replace").rstrip())

    async def read_stderr(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stderr.append(line)
            self.log.info("stderr: %s", line.decode(errors="replace").rstrip())

    async def read_log(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                size_encoded = await reader.readexactly(5)
            except asyncio.IncompleteReadError:
                break
            code, size = struct.unpack("=BL", size_encoded)
            pickled = await reader.read(size)
            decoded = pickle.loads(pickled)
            if code == RESULT_LOG:
                self.log.handle(decoded)
            elif code == RESULT_EXCEPTION:
                self.exc_info = (decoded[0], decoded[1], decoded[2].as_traceback())
            elif code == RESULT_VALUE:
                self.has_result = True
                self.result = decoded
            else:
                raise NotImplementedError(f"unknown result stream item type {code!r}")


class AsyncioRunner(Runner):
    def __init__(self, logger: logging.Logger, config: RunConfig, cmd: list[str]):
        super().__init__(logger, config)
        self.cmd = cmd

    def execute(self) -> subprocess.CompletedProcess[bytes]:
        return asyncio.run(self._run())

    async def start_process(self) -> asyncio.subprocess.Process:
        """
        Start an asyncio subprocess for the command, returning it so it can be
        supervised
        """
        raise NotImplementedError(f"{self.__class__}.start_process")

    async def _run(self) -> subprocess.CompletedProcess[bytes]:
        proc = await self.start_process()
        assert proc.stdout is not None
        assert proc.stderr is not None

        await asyncio.gather(
            self.read_stdout(proc.stdout),
            self.read_stderr(proc.stderr),
            proc.wait(),
        )
        assert proc.returncode is not None

        stdout = b"".join(self.stdout)
        stderr = b"".join(self.stderr)

        if self.config.check and proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, self.cmd, stdout, stderr)

        return subprocess.CompletedProcess(self.cmd, proc.returncode, stdout, stderr)


class LocalRunner(AsyncioRunner):
    """
    Run a command locally, logging its output
    """

    @override
    def _get_name(self) -> str:
        return shlex.join(self.cmd)

    @override
    async def start_process(self) -> asyncio.subprocess.Process:
        if self.config.user is not None:
            raise NotImplementedError("support for user config in LocalRunner is not yet implemented")
        if self.config.interactive:
            raise NotImplementedError("support for interactive config in LocalRunner is not yet implemented")

        self.log.info("Running %s", self.name)

        kwargs: dict[str, Any] = {}
        if self.config.cwd is not None:
            kwargs["cwd"] = self.config.cwd

        if self.config.use_path:
            executable = shutil.which(self.cmd[0])
            if executable is None:
                executable = self.cmd[0]
        else:
            executable = self.cmd[0]

        return await asyncio.create_subprocess_exec(
            executable, *self.cmd[1:], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs
        )

    @classmethod
    def run(
        cls,
        logger: logging.Logger,
        cmd: list[str],
        *,
        check: bool = True,
        cwd: Path | None = None,
        interactive: bool = False,
        use_path: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run a command in the host system."""
        config = RunConfig(check=check, cwd=cwd, interactive=interactive, use_path=use_path)
        runner = LocalRunner(logger, config, cmd)
        return runner.execute()
