"""
Infrastructure for running commands (in local or running systems) and logging
their output
"""
from __future__ import annotations

import asyncio
import dataclasses
import grp
import importlib
import json
import logging
import os
import pwd
import shlex
import shutil
import subprocess
import traceback
from typing import (TYPE_CHECKING, Any, Callable, Dict, List, NamedTuple,
                    Optional, TextIO, Tuple)

from . import setns

if TYPE_CHECKING:
    from .container import NspawnContainer
    from .system import SystemConfig


class UserConfig(NamedTuple):
    """
    User and group information to use for running processes
    """
    user_name: str
    user_id: int
    group_name: str
    group_id: int

    @classmethod
    def from_file(cls, pathname: str) -> UserConfig:
        """
        Instantiate a UserConfig from ownership of a file
        """
        st = os.stat(pathname)
        pw = pwd.getpwuid(st.st_uid)
        gr = grp.getgrgid(st.st_gid)
        return cls(pw.pw_name, pw.pw_uid, gr.gr_name, gr.gr_gid)

    @classmethod
    def root(cls) -> UserConfig:
        """
        Instantiate a UserConfig for the root user
        """
        return cls("root", 0, "root", 0)

    @classmethod
    def from_current(cls) -> UserConfig:
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
    def from_sudoer(cls) -> UserConfig:
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
    def from_user(cls, name: str) -> UserConfig:
        """
        Instantiate a UserConfig from the username of a local user
        """
        pw = pwd.getpwnam(name)
        gr = grp.getgrgid(pw.pw_gid)
        return cls(pw.pw_name, pw.pw_uid, gr.gr_name, gr.gr_gid)

    def check_system(self):
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
            raise RuntimeError(
                    f"container has no user {self.user_id} {self.user_name!r}"
                  ) from None

        try:
            gr = grp.getgrgid(self.group_id)
        except KeyError:
            raise RuntimeError(
                    f"container has no group {self.group_id} {self.group_name!r}"
                  ) from None

        if pw.pw_name != self.user_name:
            raise RuntimeError(f"user {self.user_id} in container is named {pw.pw_name!r}"
                               f" but outside it is named {self.user_name!r}")

        if gr.gr_name != self.group_name:
            raise RuntimeError(f"group {self.group_id} in container is named {gr.gr_name!r}"
                               f" but outside it is named {self.group_name!r}")


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
    cwd: Optional[str] = None

    # Run as the given user. Defaults to the owner of ContainerConfig.workdir,
    # if set
    user: Optional[UserConfig] = None

    # Set to true to connect to the running terminal instead of logging output
    interactive: bool = False

    # Set to true to lookup the executable in the path instead of assuming it
    # is an absolute path
    use_path: bool = False


class Runner:
    """
    Run commands in a system
    """
    def __init__(self, logger: logging.Logger, config: RunConfig):
        super().__init__()
        self.log = logger
        self.config = config
        self.stdout: List[bytes] = []
        self.stderr: List[bytes] = []

    async def read_stdout(self, reader: asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stdout.append(line)
            self.log.info("stdout: %s", line.decode(errors="replace").rstrip())

    async def read_stderr(self, reader: asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            self.stderr.append(line)
            self.log.info("stderr: %s", line.decode(errors="replace").rstrip())

    async def read_log(self, reader: asyncio.StreamReader):
        while True:
            line = await reader.readline()
            if not line:
                break
            record_args = json.loads(line)
            message = record_args.pop("msg")
            record = logging.LogRecord(
                    name=self.log.name,
                    msg="%s", args=(message,),
                    exc_info=None, **record_args)
            self.log.handle(record)


class AsyncioRunner(Runner):
    def __init__(self, logger: logging.Logger, config: RunConfig, cmd: List[str]):
        super().__init__(logger, config)
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
        if self.config.interactive:
            raise NotImplementedError("support for interactive config in LocalRunner is not yet implemented")

        self.log.info("Running %s", " ".join(shlex.quote(c) for c in self.cmd))

        kwargs = {}
        if self.config.cwd is not None:
            kwargs["cwd"] = self.config.cwd

        if self.config.use_path:
            executable = shutil.which(self.cmd[0])
            if executable is None:
                executable = self.cmd[0]
        else:
            executable = self.cmd[0]

        return await asyncio.create_subprocess_exec(
                executable, *self.cmd[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs)

    @classmethod
    def run(
            cls,
            logger: logging.Logger,
            cmd: List[str],
            config: Optional[RunConfig] = None,
            system_config: Optional[SystemConfig] = None):
        """
        Run a one-off command
        """
        if config is None:
            config = RunConfig()
        if system_config and os.path.exists(system_config.path) and config.cwd is None:
            config.cwd = system_config.path

        runner = LocalRunner(logger, config, cmd)
        return runner.execute()


class JSONStreamHandler(logging.Handler):
    """
    Serialize log records as json over a stream
    """
    def __init__(self, stream: TextIO, level=logging.NOTSET):
        super().__init__(level)
        self.stream = stream

    def emit(self, record):
        encoded = json.dumps({
            "level": record.levelno,
            "pathname": record.pathname,
            "lineno": record.lineno,
            "msg": record.msg % record.args,
        })
        print(encoded, file=self.stream)
        self.stream.flush()


class SetnsCallableRunner(Runner):
    def __init__(
            self, run: NspawnContainer, config: RunConfig, func: Callable[[], Optional[int]],
            args: Tuple[Any] = (), kwargs: Optional[Dict[str, any]] = None):
        super().__init__(run.system.log, config)
        self.run = run
        self.leader_pid = int(run.properties["Leader"])
        self.func = func
        self.args = args
        self.kwargs = kwargs

    async def make_reader(self, fd: int):
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        reader_protocol = asyncio.StreamReaderProtocol(reader)
        transport, protocol = await loop.connect_read_pipe(
                lambda: reader_protocol, os.fdopen(fd))
        return reader

    async def collect_output(self, stdout_r: Optional[int], stderr_r: Optional[int], log_r: int):
        # See https://gist.github.com/oconnor663/08c081904264043e55bf
        readers = []
        if stdout_r is not None:
            readers.append(self.read_stdout(await self.make_reader(stdout_r)))
        if stderr_r is not None:
            readers.append(self.read_stderr(await self.make_reader(stderr_r)))
        readers.append(self.read_log(await self.make_reader(log_r)))

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

    def execute(self) -> subprocess.CompletedProcess:
        func_name = self.func.__doc__.strip() if self.func.__doc__ else self.func.__name__
        self.log.info("Running %s", func_name)

        catch_output = not self.config.interactive

        if catch_output:
            # Create pipes for catching stdout and stderr
            stdout_r, stdout_w = os.pipe2(0)
            stderr_r, stderr_w = os.pipe2(0)
        log_r, log_w = os.pipe2(0)

        pid = os.fork()
        if pid == 0:
            try:
                if catch_output:
                    # Redirect /dev/null to stdin
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
                os.close(log_r)

                # Start building an environment from scratch
                env = {
                    "HOSTNAME": self.run.instance_name,
                }
                if path := os.environ.get("PATH"):
                    env["PATH"] = path
                if term := os.environ.get("TERM"):
                    env["TERM"] = term

                logging.shutdown()
                importlib.reload(logging)
                logging.getLogger().addHandler(JSONStreamHandler(stream=open(log_w, "wt"), level=logging.DEBUG))
                logging.getLogger().setLevel(logging.DEBUG)
                setns.nsenter(
                        self.leader_pid,
                        # We don't use a private user namespace in containers
                        user=False)

                user = self.set_user()
                env["USER"] = user.user_name
                env["LOGNAME"] = user.user_name
                if user.user_id == 0:
                    env["HOME"] = "/root"
                else:
                    env["HOME"] = f"/home/{user.user_name}"

                if self.config.cwd is not None:
                    os.chdir(self.config.cwd)
                    env["PWD"] = self.config.cwd

                # Replace environment
                os.environ.clear()
                for k, v in env.items():
                    os.environ[k] = v

                # Refork to actually enter the PID namespace
                pid = os.fork()
                if pid == 0:
                    try:
                        res = self.func(*self.args, **({} if self.kwargs is None else self.kwargs))
                        os._exit(res if res is not None else 0)
                    except Exception:
                        traceback.print_exc()
                        # Reusing systemd-analyze exit-status
                        os._exit(255)
                else:
                    wres = os.waitid(os.P_PID, pid, os.WEXITED)
                    os._exit(wres.si_status)
            except Exception:
                traceback.print_exc()
                # Reusing systemd-analyze exit-status
                os._exit(255)
        else:
            if catch_output:
                os.close(stdout_w)
                os.close(stderr_w)
            os.close(log_w)

        stdout: Optional[bytes]
        stderr: Optional[bytes]
        if catch_output:
            asyncio.run(self.collect_output(stdout_r, stderr_r, log_r))
            stdout = b"".join(self.stdout)
            stderr = b"".join(self.stderr)
        else:
            asyncio.run(self.collect_output(None, None, log_r))
            stdout = None
            stderr = None

        wres = os.waitid(os.P_PID, pid, os.WEXITED)
        if self.config.check and wres.si_status != 0:
            raise subprocess.CalledProcessError(
                    wres.si_status, func_name, stdout, stderr)

        return subprocess.CompletedProcess(
                func_name, wres.si_status, stdout, stderr)
