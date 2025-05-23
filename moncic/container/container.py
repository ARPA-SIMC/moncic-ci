import abc
import grp
import logging
import os
import pwd
import shlex
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from functools import cached_property
from pathlib import Path
from typing import Any, ContextManager, TypeVar

from moncic.image import RunnableImage
from moncic.runner import CompletedCallable, RunConfig, UserConfig
from moncic.utils import libbanana
from moncic.utils.script import Script

from .binds import BindConfig
from .config import ContainerConfig

Result = TypeVar("Result")

log = logging.getLogger(__name__)

# PID-specific sequence number used for machine names
machine_name_sequence_pid: int | None = None
machine_name_sequence: int = 0

# Convert PIDs to machine names
machine_name_generator = libbanana.Codec(
    alphabets=(
        "bcdfgjklmnprstvwxyz",
        "aeiou",
    )
).encode


class Container(abc.ABC):
    """
    An instance of an Image in execution as a container
    """

    def __init__(self, image: RunnableImage, *, config: ContainerConfig, instance_name: str | None = None):
        config.check()
        self.stack = ExitStack()
        self.image = image
        self.config = config
        self.started = False
        #: Default to False, set to True to leave the container running on exit
        self.linger: bool = False
        #: User-provided instance name
        self._instance_name = instance_name
        #: Host directory used for supporting container interactions
        self.workdir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        #: Exchange directory for scripts
        self.scriptdir = self.workdir / "scripts"
        self.scriptdir.mkdir(parents=True, exist_ok=True)
        self.mounted_scriptdir = Path("/srv/moncic-ci/scripts")

    @cached_property
    def instance_name(self) -> str:
        """
        Name of the running container instance, which can be used to access it
        with normal user commands
        """
        if self._instance_name:
            return self._instance_name
        return self.get_instance_name()

    @cached_property
    def logger(self) -> logging.Logger:
        """
        Return a logger for this system
        """
        return logging.getLogger(f"container.{self.instance_name}")

    def host_run(
        self, cmd: list[str], check: bool = True, cwd: Path | None = None, interactive: bool = False
    ) -> subprocess.CompletedProcess:
        """Run a command in the host system."""
        from moncic.runner import LocalRunner

        return LocalRunner.run(self.logger, cmd, check=check, cwd=cwd, interactive=interactive)

    def get_instance_name(self) -> str:
        """Compute an instance name when none was provided in constructor."""
        global machine_name_sequence_pid, machine_name_sequence
        current_pid = os.getpid()
        if machine_name_sequence_pid is None or machine_name_sequence_pid != current_pid:
            machine_name_sequence_pid = current_pid
            machine_name_sequence = 0

        seq = machine_name_sequence
        machine_name_sequence += 1
        instance_name = "mc-" + machine_name_generator(current_pid)
        if seq > 0:
            instance_name += str(seq)
        return instance_name

    def __enter__(self):
        self.stack.__enter__()
        for bind in self.config.binds:
            self.stack.enter_context(bind.host_setup(self))
        self.stack.enter_context(self._container())

        # Do user forwarding if requested
        if self.config.forward_user:
            self.forward_user(self.config.forward_user)
        # We do not need to delete the user if it was created, because we
        # enforce that forward_user is only used on ephemeral containers

        for bind in self.config.binds:
            self.stack.enter_context(bind.guest_setup(self))
        self.started = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.started = False
        if self.linger:
            return
        self.stack.__exit__(exc_type, exc_val, exc_tb)

    @abc.abstractmethod
    def _container(self) -> ContextManager[None]:
        """Start the container for the duration of the context manager."""

    def forward_user(self, user: UserConfig, allow_maint: bool = False):
        """
        Ensure the system has a matching user and group
        """
        check_script = Script("Check user IDs in container")
        check_script.run_unquoted(f"UID=$(id -u {user.user_id} || true)")
        check_script.run_unquoted(f"GID=$(id -g {user.user_id} || true)")
        check_script.run_unquoted('echo "$UID:$GID"')

        res = self.run_script(check_script, config=RunConfig(cwd=Path("/"), user=UserConfig.root()))
        uid, gid = res.stdout.strip().decode().split(":")

        has_user = uid and int(uid) == user.user_id
        if not has_user and not allow_maint and not self.config.ephemeral:
            raise RuntimeError(f"user {user.user_name} not found in non-ephemeral containers")

        has_group = gid and int(gid) == user.group_id
        if not has_group and not allow_maint and not self.config.ephemeral:
            raise RuntimeError(f"user group {user.group_name} not found in non-ephemeral containers")

        if not has_user and not has_group:
            setup_script = Script("Set up local user")
            setup_script.run(["groupadd", "--gid", str(user.group_id), user.group_name])
            setup_script.run(
                [
                    "useradd",
                    "--create-home",
                    "--uid",
                    str(user.user_id),
                    "--gid",
                    str(user.group_id),
                    user.user_name,
                ],
            )
            self.run_script(setup_script, config=RunConfig(cwd=Path("/"), user=UserConfig.root()))
        else:
            script = Script("Validate user database")
            with script.if_("[ $(id -u) -eq 0 ] && [ $(id -g) -eq 0 ]"):
                script.run(["exit", "0"])

            script.run_unquoted(f'UNAME="$(id -un {user.user_id})"')
            with script.if_('[ "$UNAME" != {shlex.quote(user.user_name)} ]'):
                script.fail(
                    f"user {user.user_id} in container is named $UNAME but outside it is named {user.user_name}"
                )

            script.run_unquoted('''GNAME="$(getent group {user.group_id} | sed -r 's/:.+//')"''')
            with script.if_('[ "$GNAME" != {shlex.quote(user.group_name)} ]'):
                script.fail(
                    f"group {user.group_id} in container is named $GNAME but outside it is named {user.group_name}"
                )

            self.run_script(script, config=RunConfig(cwd=Path("/"), user=UserConfig.root()))

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

    @abc.abstractmethod
    def get_root(self) -> Path:
        """Return the path to the root directory of this container."""

    @abc.abstractmethod
    def get_pid(self) -> int:
        """Return the PID of the main container process."""

    @abc.abstractmethod
    def binds(self) -> Iterator[BindConfig]:
        """
        Iterate the bind mounts active on this container
        """

    @abc.abstractmethod
    def run(self, command: list[str], config: RunConfig | None = None) -> CompletedCallable:
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

    def run_script(self, script: str | Script, config: RunConfig | None = None) -> CompletedCallable:
        """
        Run the given Script or string as a script in the machine.

        A shebang at the beginning of the script will be honored.

        Returns the process exit status.
        """
        run_config = self.config.run_config(config)

        with tempfile.NamedTemporaryFile("w+t", dir=self.scriptdir, delete_on_close=False) as tf:
            if isinstance(script, Script):
                self.image.logger.info("Running script %s", script.title)
                script.print(file=tf)
            else:
                if len(script) > 200:
                    self.image.logger.info("Running script %r…", script[:200])
                else:
                    self.image.logger.info("Running script %r", script)
                tf.write(script)
            os.fchmod(tf.fileno(), 0o755)
            tf.close()
            return self.run([(self.mounted_scriptdir / os.path.basename(tf.name)).as_posix()], run_config)

    @abc.abstractmethod
    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        """
        Run the given callable in a separate process inside the running
        system. Returns a CompletedCallable describing details of the execution
        """

    def run_callable(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> Result:
        """
        Run the given callable in a separate process inside the running
        system. Returns the function's result
        """
        completed = self.run_callable_raw(func, config, args, kwargs)
        return completed.result()

    def run_shell(self, config: RunConfig | None) -> CompletedCallable:
        """
        Open a shell in the container
        """
        shell_candidates = []
        if "SHELL" in os.environ:
            shell_candidates.append(os.environ["SHELL"])
            shell_candidates.append(os.path.basename(os.environ["SHELL"]))
        shell_candidates.extend(("bash", "sh"))

        script = f"""#!/bin/sh

for candidate in {shlex.join(shell_candidates)}
do
    command -v $candidate && break
done
"""
        res = self.run_script(script)
        shell = res.stdout.strip().decode()
        if not shell:
            raise RuntimeError(f"No valid shell found. Tried: {shlex.join(shell_candidates)}")

        return self.run([shell, "--login"], config=config)


class MaintenanceContainer(Container, abc.ABC):
    """Non-ephemeral container used for maintenance."""
