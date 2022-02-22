from __future__ import annotations
import contextlib
import dataclasses
import errno
import logging
import os
import shlex
import signal
import subprocess
import tempfile
import time
from typing import List, Optional, Callable, ContextManager, Protocol, TYPE_CHECKING
import uuid

from .runner import SetnsCallableRunner, RunConfig
from .nspawn import escape_bind_ro
if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)


@dataclasses.dataclass
class ContainerConfig:
    """
    Configuration needed to customize starting a container
    """
    # If true, changes done to the container filesystem will not persist
    ephemeral: bool = True

    # Bind mount this directory in the running system and use it as default
    # working directory
    workdir: Optional[str] = None

    # systemd-nspawn --bind pathspecs to bind read-write
    bind: List[str] = dataclasses.field(default_factory=list)

    # systemd-nspawn --bind_ro pathspecs to bind read-only
    bind_ro: List[str] = dataclasses.field(default_factory=list)

    def run_config(self, run_config: Optional[RunConfig] = None) -> RunConfig:
        if run_config is None:
            res = RunConfig()
        else:
            res = run_config

        if res.cwd is None and self.workdir is not None:
            name = os.path.basename(self.workdir)
            res.cwd = f"/root/{name}"

        if self.workdir is not None and (res.user is None or res.group is None):
            st = os.stat(self.workdir)
            if res.user is None:
                res.user = st.st_uid
            if res.group is None:
                res.group = st.st_gid

        return res


class Container(ContextManager, Protocol):
    """
    An instance of a System in execution as a container
    """
    system: System
    config: ContainerConfig

    def run(self, command: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        """
        Run the given command inside the running system.

        Returns a dict with:
        {
            "stdout": bytes,
            "stderr": bytes,
            "returncode": int,
        }

        stdout and stderr are logged in real time as the process is running.
        """
        ...

    def run_script(self, body: str, config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        """
        Run the given string as a script in the machine.

        A shebang at the beginning of the script will be honored.

        Returns the process exit status.
        """
        ...

    def run_callable(
            self, func: Callable[[], Optional[int]], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        """
        Run the given callable in a separate process inside the running
        system. Returns the process exit status.
        """
        ...

    def start(self):
        """
        Start the running system
        """
        ...

    def terminate(self) -> None:
        """
        Shut down the running system
        """
        ...


class ContainerBase(contextlib.ExitStack):
    """
    Convenience common base implementation for Container
    """
    def __init__(self, system: System, instance_name: Optional[str] = None, config: Optional[ContainerConfig] = None):
        super().__init__()
        self.system = system

        if instance_name is None:
            self.instance_name = str(uuid.uuid4())
        else:
            self.instance_name = instance_name

        if config is None:
            config = ContainerConfig()
        self.config = config

        self.started = False

    def __enter__(self):
        self.start()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.terminate()
        return super().__exit__(exc_type, exc_value, exc_tb)


class NspawnContainer(ContainerBase):
    """
    Running system implemented using systemd nspawn
    """
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        # machinectl properties of the running machine
        self.properties = None

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

        self.system.log.info("Running %s", " ".join(shlex.quote(c) for c in systemd_run_cmd))
        res = subprocess.run(systemd_run_cmd, capture_output=True)
        if res.returncode != 0:
            self.system.log.error("Failed to run %s (exit code %d): %r",
                                  " ".join(shlex.quote(c) for c in systemd_run_cmd),
                                  res.returncode,
                                  res.stderr)
            raise RuntimeError("Failed to start container")

    def get_start_command(self):
        cmd = [
            "systemd-nspawn",
            "--quiet",
            f"--directory={self.system.path}",
            f"--machine={self.instance_name}",
            "--boot",
            "--notify-ready=yes",
        ]
        if self.config.workdir is not None:
            workdir = os.path.abspath(self.config.workdir)
            name = os.path.basename(workdir)
            if name.startswith("."):
                raise RuntimeError(f"Repository directory name {name!r} cannot start with a dot")
            cmd.append(f"--bind={escape_bind_ro(workdir)}:/root/{escape_bind_ro(name)}")
        if self.config.bind:
            for pathspec in self.config.bind:
                cmd.append("--bind=" + pathspec)
        if self.config.bind_ro:
            for pathspec in self.config.bind_ro:
                cmd.append("--bind-ro=" + pathspec)
        if self.config.ephemeral:
            cmd.append("--ephemeral")
        return cmd

    def start(self):
        if self.started:
            return

        self.system.log.info("Starting system %s as %s using image %s",
                             self.system.name, self.instance_name, self.system.path)

        cmd = self.get_start_command()

        self._run_nspawn(cmd)
        self.started = True

        # Read machine properties
        res = subprocess.run(
                ["machinectl", "show", self.instance_name],
                capture_output=True, text=True, check=True)
        self.properties = {}
        for line in res.stdout.splitlines():
            key, value = line.split('=', 1)
            self.properties[key] = value

    def run(self, command: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        def command_runner():
            os.execv(command[0], command)

        command_runner.__doc__ = " ".join(shlex.quote(c) for c in command)

        return self.run_callable(command_runner, config)

    def run_script(self, body: str, config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        def script_runner():
            with tempfile.TemporaryDirectory() as workdir:
                script_path = os.path.join(workdir, "script")
                with open(script_path, "wt") as fd:
                    fd.write(body)
                    fd.flush()
                    os.chmod(fd.fileno(), 0o700)
                # FIXME: if cwd is set in config, don't chdir here
                #        and don't use a working directory
                os.chdir(workdir)
                os.execv(script_path, [script_path])

        if len(body) > 200:
            script_runner.__doc__ = f"script: {body[:200]!r}…"
        else:
            script_runner.__doc__ = f"script: {body!r}"

        return self.run_callable(script_runner, config)

    def run_callable(
            self, func: Callable[[], Optional[int]], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        run_config = self.config.run_config(config)
        runner = SetnsCallableRunner(self, run_config, func)
        return runner.execute()

    def terminate(self):
        if not self.started:
            return

        # See https://github.com/systemd/systemd/issues/6458
        leader_pid = int(self.properties["Leader"])
        os.kill(leader_pid, signal.SIGRTMIN + 4)
        while True:
            try:
                os.kill(leader_pid, 0)
            except OSError as e:
                if e.errno == errno.ESRCH:
                    break
                raise
            time.sleep(0.1)

        # res = subprocess.run(["machinectl", "stop", self.instance_name])
        # if res.returncode != 0:
        #     raise RuntimeError(f"Terminating machine {self.instance_name} failed with code {res.returncode}")
        self.started = False
