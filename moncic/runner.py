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
import sys
import types
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, BinaryIO, Generic, NamedTuple, Self, TypeVar, cast, override

import tblib

from . import context
from .utils import guest, setns

if TYPE_CHECKING:
    from .container import Container


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


class CompletedCallable(Generic[Result], subprocess.CompletedProcess[bytes]):
    """
    Extension of subprocess.CompletedProcess that can also store a return value
    and exception information
    """

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.returnvalue: Result | None = None
        self.exc_info: tuple[type[BaseException], BaseException, types.TracebackType] | None = None

    def result(self) -> Result:
        """
        Return the callable's return value if it was successful, or if it
        raised an exception, reraise that exception
        """
        if self.exc_info:
            raise self.exc_info[1].with_traceback(self.exc_info[2])
        else:
            return cast(Result, self.returnvalue)


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


class PickleStreamHandler(logging.Handler):
    """
    Serialize log records as json over a stream
    """

    def __init__(self, logger_name_prefix: str, stream: IO[bytes], level: Any = logging.NOTSET) -> None:
        super().__init__(level)
        self.logger_name_prefix = logger_name_prefix
        self.stream = stream

    @override
    def emit(self, record: logging.LogRecord) -> None:
        record.name = self.logger_name_prefix + "." + record.name
        pickled = pickle.dumps(record, pickle.HIGHEST_PROTOCOL)
        self.stream.write(struct.pack("=BL", RESULT_LOG, len(pickled)))
        self.stream.write(pickled)
        self.stream.flush()


class SetnsCallableRunner(Generic[Result], Runner):
    def __init__(
        self,
        container: "Container",
        config: RunConfig,
        func: Callable[..., Result],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ):
        super().__init__(container.image.logger, config)
        self.container = container
        self.leader_pid = container.get_pid()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.result_stream_writer: BinaryIO

    @override
    def _get_name(self) -> str:
        return self.func.__doc__.strip() if self.func.__doc__ else self.func.__name__

    async def make_reader(self, fd: int) -> asyncio.StreamReader:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        reader_protocol = asyncio.StreamReaderProtocol(reader)
        transport, protocol = await loop.connect_read_pipe(lambda: reader_protocol, os.fdopen(fd))
        return reader

    async def collect_output(self, stdout_r: int | None, stderr_r: int | None, result_r: int) -> None:
        # See https://gist.github.com/oconnor663/08c081904264043e55bf
        readers = []
        if stdout_r is not None:
            readers.append(self.read_stdout(await self.make_reader(stdout_r)))
        if stderr_r is not None:
            readers.append(self.read_stderr(await self.make_reader(stderr_r)))
        readers.append(self.read_log(await self.make_reader(result_r)))

        await asyncio.gather(*readers)

    def set_user(self) -> UserConfig:
        """
        Set the user if requested, and return a UserConfig for the current user
        """
        if self.config.user is None:
            return UserConfig.from_current()

        self.config.user.check_system()

        gid = self.config.user.group_id
        os.setresgid(gid, gid, gid)

        uid = self.config.user.user_id
        os.setresuid(uid, uid, uid)

        return self.config.user

    def send_exception(self) -> None:
        """
        Send the current exception to the result stream
        """
        exc_info = sys.exc_info()
        pickled = pickle.dumps((exc_info[0], exc_info[1], tblib.Traceback(exc_info[2])), pickle.HIGHEST_PROTOCOL)
        self.result_stream_writer.write(struct.pack("=BL", RESULT_EXCEPTION, len(pickled)))
        self.result_stream_writer.write(pickled)
        self.result_stream_writer.flush()

    def send_result(self, value: Any) -> None:
        """
        Send a function return value to the result stream
        """
        pickled = pickle.dumps(value, pickle.HIGHEST_PROTOCOL)
        self.result_stream_writer.write(struct.pack("=BL", RESULT_VALUE, len(pickled)))
        self.result_stream_writer.write(pickled)
        self.result_stream_writer.flush()

    def execute(self) -> CompletedCallable[Result]:
        self.log.info("Running %s", self.name)

        catch_output = not self.config.interactive

        if catch_output:
            # Create pipes for catching stdout and stderr
            stdout_r, stdout_w = os.pipe2(0)
            stderr_r, stderr_w = os.pipe2(0)
        # Pipe to forward logging and return results
        result_r, result_w = os.pipe2(0)

        pid = os.fork()
        if pid == 0:
            try:
                if catch_output:
                    # Have stdin read from /dev/null
                    fd = os.open("/dev/null", os.O_RDONLY)
                    os.dup2(fd, 0)
                    os.close(fd)

                    os.close(stdout_r)
                    os.close(stderr_r)
                    # Redirect stdout and stderr to the pipes to parent
                    os.dup2(stdout_w, 1)
                    os.close(stdout_w)
                    os.dup2(stderr_w, 2)
                    os.close(stderr_w)
                os.close(result_r)
                self.result_stream_writer = open(result_w, "wb")

                # Start building an environment from scratch
                env = {
                    "HOSTNAME": self.container.instance_name,
                }
                if path := os.environ.get("PATH"):
                    env["PATH"] = path
                if term := os.environ.get("TERM"):
                    env["TERM"] = term

                # Setup root logger to divert all logging to our forwarded
                #
                # The logic comes from unittest.TestCase.assertLogs
                root_logger = logging.getLogger()
                root_logger.handlers = [
                    PickleStreamHandler(
                        logger_name_prefix=self.log.name, stream=self.result_stream_writer, level=logging.DEBUG
                    )
                ]
                root_logger.setLevel(logging.DEBUG)
                root_logger.propagate = False

                setns.nsenter(
                    self.leader_pid,
                    # We don't use a private user namespace in containers
                    user=False,
                )

                user = self.set_user()
                env["USER"] = user.user_name
                env["LOGNAME"] = user.user_name
                if user.user_id == 0:
                    env["HOME"] = "/root"
                else:
                    env["HOME"] = f"/home/{user.user_name}"

                if self.config.cwd is not None:
                    os.chdir(self.config.cwd)
                    env["PWD"] = self.config.cwd.as_posix()

                # Replace environment
                os.environ.clear()
                for k, v in env.items():
                    os.environ[k] = str(v)

                # Refork to actually enter the PID namespace
                pid = os.fork()
                if pid == 0:
                    context.image.set(self.container.image)
                    context.container.set(self.container)
                    try:
                        guest.in_guest = True
                        if self.kwargs is None:
                            res = self.func(*self.args)
                        else:
                            res = self.func(*self.args, **self.kwargs)
                    except BaseException:
                        self.send_exception()
                        # Reusing systemd-analyze exit-status
                        os._exit(0)
                    else:
                        self.send_result(res)
                        os._exit(0)
                else:
                    # Forward the child return code
                    wres = os.waitid(os.P_PID, pid, os.WEXITED)
                    assert wres is not None
                    os._exit(wres.si_status)
            except BaseException:
                self.send_exception()
                # Reusing systemd-analyze exit-status
                os._exit(255)
        else:
            if catch_output:
                os.close(stdout_w)
                os.close(stderr_w)
            os.close(result_w)

        stdout: bytes | None
        stderr: bytes | None
        if catch_output:
            asyncio.run(self.collect_output(stdout_r, stderr_r, result_r))
            stdout = b"".join(self.stdout)
            stderr = b"".join(self.stderr)
        else:
            asyncio.run(self.collect_output(None, None, result_r))
            stdout = None
            stderr = None

        wres = os.waitid(os.P_PID, pid, os.WEXITED)
        assert wres is not None
        if self.exc_info and wres.si_status == 255:
            raise self.exc_info[1].with_traceback(self.exc_info[2])

        cres = CompletedCallable[Result](self.name, wres.si_status, stdout, stderr)
        cres.exc_info = self.exc_info
        cres.returnvalue = self.result
        return cres
