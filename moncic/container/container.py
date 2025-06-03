import abc
import logging
import os
import shlex
import subprocess
import tempfile
import types
from collections.abc import Generator, Iterator
from contextlib import ExitStack, contextmanager
from functools import cached_property
from pathlib import Path
from typing import ContextManager, Self

from moncic.image import RunnableImage
from moncic.runner import UserConfig
from moncic.utils import libbanana
from moncic.utils.script import Script

from .binds import BindConfig
from .config import ContainerConfig, RunConfig

log = logging.getLogger(__name__)

# PID-specific sequence number used for machine names
machine_name_sequence_pid: int | None = None
machine_name_sequence: int = 0

# Convert PIDs to machine names
machine_name_generator = libbanana.Codec(
    alphabets=(
        list("bcdfgjklmnprstvwxyz"),
        list("aeiou"),
    )
).encode


class ContainerCannotStart(Exception):
    """Exception raised when a container cannot be started."""


class Container(abc.ABC):
    """
    An instance of an Image in execution as a container
    """

    def __init__(
        self, image: RunnableImage, *, config: ContainerConfig, instance_name: str | None = None, ephemeral: bool = True
    ):
        config.check()
        self.ephemeral = ephemeral
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
        self.guest_scriptdir = Path("/srv/moncic-ci/scripts")

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
        self, cmd: list[str], check: bool = True, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        """Run a command in the host system."""
        from moncic.runner import Runner

        runner = Runner(self.logger, cmd, cwd=cwd, check=check)
        return runner.run()

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

    def __enter__(self) -> Self:
        self.stack.__enter__()
        self.stack.enter_context(self.config.host_setup(self))
        self.stack.enter_context(self._container())

        # Do user forwarding if requested
        if self.config.forward_user:
            self.forward_user(self.config.forward_user)
        # We do not need to delete the user if it was created, because we
        # enforce that forward_user is only used on ephemeral containers

        self.stack.enter_context(self.config.guest_setup(self))
        self.started = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.started = False
        if self.linger:
            return
        self.stack.__exit__(exc_type, exc_val, exc_tb)

    @abc.abstractmethod
    def _container(self) -> ContextManager[None]:
        """Start the container for the duration of the context manager."""

    def forward_user(self, user: UserConfig, allow_maint: bool = False) -> None:
        """
        Ensure the system has a matching user and group
        """
        check_script = Script("Check user IDs in container", cwd=Path("/"), user=UserConfig.root())
        check_script.run_unquoted(f"USER_ID=$(id -u {user.user_id} 2>/dev/null || true)")
        check_script.run_unquoted(f"GROUP_ID=$(id -g {user.user_id} 2>/dev/null || true)")
        check_script.run_unquoted(f"USER_NAME=$(id -un {user.user_id} 2>/dev/null || true)")
        check_script.run_unquoted(f"GROUP_NAME=$(id -gn {user.user_id} 2>/dev/null || true)")
        check_script.run_unquoted(
            f"""HAS_USER=$(test -n "$(getent passwd {shlex.quote(user.user_name)})" && echo 'yes' || echo 'no')"""
        )
        check_script.run_unquoted(
            f"""HAS_GROUP=$(test -n "$(getent group {shlex.quote(user.group_name)})" && echo 'yes' || echo 'no')"""
        )
        check_script.run_unquoted('echo "$USER_ID:$GROUP_ID:$USER_NAME:$GROUP_NAME:$HAS_USER:$HAS_GROUP"')

        res = self.run_script(check_script)
        uid, gid, user_name, group_name, user_in_db, group_in_db = res.stdout.strip().decode().split(":")

        has_user = uid and int(uid) == user.user_id
        if not has_user and not allow_maint and not self.ephemeral:
            raise RuntimeError(f"user {user.user_name} not found in non-ephemeral containers")

        has_group = gid and int(gid) == user.group_id
        if not has_group and not allow_maint and not self.ephemeral:
            raise RuntimeError(f"user group {user.group_name} not found in non-ephemeral containers")

        setup_script = Script("Set up local user", cwd=Path("/"), user=UserConfig.root())

        create_user = not has_user or user_in_db == "no"
        create_group = not has_group or group_in_db == "no"
        if (user_name and user_name != user.user_name) or (group_name and group_name != user.group_name):
            setup_script.run(["userdel", user_name])
            setup_script.run(["groupdel", group_name], check=False)
            create_user = True
            create_group = True
        if create_group:
            setup_script.run(["groupadd", "--gid", str(user.group_id), user.group_name])
        if create_user:
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
        if setup_script:
            self.run_script(setup_script)

        script = Script("Validate user database", cwd=Path("/"), user=user)
        with script.if_("[ $(id -u) -eq 0 ] && [ $(id -g) -eq 0 ]"):
            script.run(["exit", "0"])

        script.run_unquoted(f'UNAME="$(id -un {user.user_id})"')
        with script.if_(f'[ "$UNAME" != {shlex.quote(user.user_name)} ]'):
            script.fail(f"user {user.user_id} in container is named $UNAME but outside it is named {user.user_name}")

        script.run_unquoted(f'''GNAME="$(getent group {user.group_id} | sed -r 's/:.+//')"''')
        with script.if_(f'[ "$GNAME" != {shlex.quote(user.group_name)} ]'):
            script.fail(f"group {user.group_id} in container is named $GNAME but outside it is named {user.group_name}")

        self.run_script(script)

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
    def run(self, command: list[str], config: RunConfig | None = None) -> subprocess.CompletedProcess[bytes]:
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

    @contextmanager
    def script_in_guest(self, script: Script) -> Generator[Path, None, None]:
        """Send the script to the container, returning its guest path."""
        with tempfile.NamedTemporaryFile("w+t", dir=self.scriptdir, delete_on_close=False) as tf:
            script.print(file=tf)
            os.fchmod(tf.fileno(), 0o755)
            tf.close()
            yield self.guest_scriptdir / os.path.basename(tf.name)

    @abc.abstractmethod
    def run_script(self, script: Script, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        """
        Run the given Script or string as a script in the machine.

        A shebang at the beginning of the script will be honored.

        Returns the process exit status.
        """

    def run_shell(self, config: RunConfig | None) -> subprocess.CompletedProcess[bytes]:
        """
        Open a shell in the container
        """
        if config is None:
            config = RunConfig()
        config.interactive = True
        config.check = False
        if config.cwd is None:
            config.cwd = self.config.get_default_cwd()
        if config.user is None:
            config.user = self.config.get_default_user()

        shell_candidates = []
        if "SHELL" in os.environ:
            shell_candidates.append(os.environ["SHELL"])
            shell_candidates.append(os.path.basename(os.environ["SHELL"]))
        shell_candidates.extend(("bash", "sh"))

        script = Script("Find a usable shell")
        with script.for_("candidate", shell_candidates):
            script.run_unquoted('command -v "$candidate" && break')

        res = self.run_script(script)
        shell = res.stdout.strip().decode()
        if not shell:
            raise RuntimeError(f"No valid shell found. Tried: {shlex.join(shell_candidates)}")

        return self.run([shell, "--login"], config=config)


class MaintenanceContainer(Container, abc.ABC):
    """Non-ephemeral container used for maintenance."""

    def __init__(self, image: RunnableImage, *, config: ContainerConfig, instance_name: str | None = None):
        super().__init__(image, config=config, instance_name=instance_name, ephemeral=False)
