"""
Infrastructure for running commands (in local or running systems) and logging
their output
"""

import asyncio
import grp
import logging
import os
import pwd
import shlex
import shutil
import subprocess
from functools import cached_property
from pathlib import Path
from typing import Any, NamedTuple, Self, TypeVar

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


class Runner:
    """Run a command, logging its output in realtime."""

    def __init__(self, logger: logging.Logger, cmd: list[str], cwd: Path | None = None, check: bool = True):
        super().__init__()
        self.logger = logger
        self.cmd = cmd
        self.cwd = cwd
        self.check = check
        self.stdout: list[bytes] = []
        self.stderr: list[bytes] = []
        self.result: Any = None

    @cached_property
    def name(self) -> str:
        """Return a name describing this runner."""
        return shlex.join(self.cmd)

    def run(self) -> subprocess.CompletedProcess[bytes]:
        """Run the command and return its result."""
        return asyncio.run(self._run())

    async def read_stdout(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stdout.append(line)
            self.logger.info("stdout: %s", line.decode(errors="replace").rstrip())

    async def read_stderr(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stderr.append(line)
            self.logger.info("stderr: %s", line.decode(errors="replace").rstrip())

    async def start_process(self) -> asyncio.subprocess.Process:
        self.logger.info("Running %s", self.name)

        kwargs: dict[str, Any] = {}
        if self.cwd is not None:
            kwargs["cwd"] = self.cwd

        if self.cmd[0].startswith("/"):
            executable = self.cmd[0]
        elif found := shutil.which(self.cmd[0]):
            executable = found
        else:
            executable = self.cmd[0]

        return await asyncio.create_subprocess_exec(
            executable, *self.cmd[1:], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs
        )

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

        if self.check and proc.returncode != 0:
            self.logger.error("%s: exited with status %d", self.name, proc.returncode)
            raise subprocess.CalledProcessError(proc.returncode, self.cmd, stdout, stderr)

        return subprocess.CompletedProcess(self.cmd, proc.returncode, stdout, stderr)
