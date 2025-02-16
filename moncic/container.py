from __future__ import annotations

import dataclasses
import errno
import hashlib
import logging
import os
import pwd
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ContextManager, NoReturn, Protocol, TypeVar

from .runner import CompletedCallable, RunConfig, SetnsCallableRunner, UserConfig
from .utils import libbanana
from .utils.deb import apt_get_cmd
from .utils.nspawn import escape_bind_ro

if TYPE_CHECKING:
    from .system import System

Result = TypeVar("Result")

log = logging.getLogger(__name__)

re_split_bind = re.compile(r"(?<!\\):")

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


@dataclasses.dataclass
class BindConfig:
    """
    Configuration of one bind mount requested on the container
    """

    # Directory in the host system to be bind mounted in the container
    #
    # The source path may optionally be prefixed with a "+" character. If
    # so, the source path is taken relative to the image's root directory.
    # This permits setting up bind mounts within the container image.
    #
    # The source path may be specified as empty string, in which case a
    # temporary directory below the host's /var/tmp/ directory is used.
    # It is automatically removed when the container is shut down.
    source: str

    # Directory inside the container where the directory gets bind mounted
    destination: str

    # Type of bind mount:
    #
    # * ``ro``
    # * ``rw``
    bind_type: str = "rw"

    # Mount options for nspawn
    #
    # Mount options are comma-separated.  rbind and norbind control whether
    # to create a recursive or a regular bind mount. Defaults to "rbind".
    #
    # idmap and noidmap control if the bind mount should use filesystem id
    # mappings. Using this option requires support by the source filesystem
    # for id mappings. Defaults to "noidmap".
    mount_options: list[str] = dataclasses.field(default_factory=list)

    # If true, use this as the default working directory when running code or
    # programs in the container
    cwd: bool = False

    # Setup hook to be run at container startup inside the container
    setup: Callable[[BindConfig], None] | None = None

    # Setup hook to be run before container shutdown inside the container
    teardown: Callable[[BindConfig], None] | None = None

    def to_nspawn(self) -> str:
        """
        Return the nspawn --bind* option for this bind
        """
        if self.bind_type in ("ro",):
            option = "--bind-ro="
        elif self.bind_type in ("rw",):
            option = "--bind="
        else:
            raise ValueError(f"{self.bind_type!r}: invalid bind type")

        if self.mount_options:
            return option + ":".join(
                (escape_bind_ro(self.source), escape_bind_ro(self.destination), ",".join(self.mount_options))
            )
        else:
            if self.source == self.destination:
                return option + escape_bind_ro(self.source)
            else:
                return option + (escape_bind_ro(self.source) + ":" + escape_bind_ro(self.destination))

    @classmethod
    def create(cls, source: str, destination: str, bind_type: str, **kw) -> BindConfig:
        """
        Create a BindConfig.

        ``bind_type`` has extra values over BindConfig.bind_type and can be:

        * ``ro``: read only
        * ``rw``: read-write
        * ``volatile``: readonly with an tempfs volatile overlay
        * ``aptcache``: shared /var/cache/apt/archives mount
        * ``aptpackages``: create a local mirror with the packages in the
                           source directory, and add it to apt's sources
        """
        if bind_type == "volatile":
            kw["bind_type"] = "ro"
            kw.setdefault("setup", BindConfig.bind_hook_setup_volatile)
        elif bind_type == "aptcache":
            kw["bind_type"] = "rw"
            kw.setdefault("setup", BindConfig.bind_hook_setup_aptcache)
            kw.setdefault("teardown", BindConfig.bind_hook_teardown_aptcache)
        elif bind_type == "aptpackages":
            kw["bind_type"] = "ro"
            kw.setdefault("setup", BindConfig.bind_hook_setup_aptpackages)
            kw.setdefault("teardown", BindConfig.bind_hook_teardown_aptpackages)
        else:
            kw["bind_type"] = bind_type

        return cls(source=source, destination=destination, **kw)

    @classmethod
    def from_nspawn(cls, entry: str, bind_type: str) -> BindConfig:
        """
        Create a BindConfig from an nspawn --bind/--bind-ro option.

        ``bind_type`` is passed verbatim to BindConfig.create
        """
        # Backslash escapes are interpreted, so "\:" may be used to embed colons in either path.
        #
        parts = re_split_bind.split(entry)
        if len(parts) == 1:
            # a path argument — in which case the specified path will be
            # mounted from the host to the same path in the container
            path = parts[0].replace(r"\:", ":")
            return cls.create(path, path, bind_type)
        elif len(parts) == 2:
            # a colon-separated pair of paths — in which case the first
            # specified path is the source in the host, and the second path is
            # the destination in the container
            return cls.create(parts[0].replace(r"\:", ":"), parts[1].replace(r"\:", ":"), bind_type)
        elif len(parts) == 3:
            # a colon-separated triple of source path, destination path and mount options
            return cls.create(
                parts[0].replace(r"\:", ":"), parts[1].replace(r"\:", ":"), bind_type, mount_options=parts[2].split(",")
            )
        else:
            raise ValueError(f"{entry!r}: unparsable bind option")

    @classmethod
    def bind_hook_setup_volatile(cls, bind_config: BindConfig):
        """
        Finish setting up volatile binds in the container
        """
        volatile_root = "/run/volatile"
        os.makedirs(volatile_root, exist_ok=True)

        st = os.stat(bind_config.destination)

        m = hashlib.sha1()
        m.update(bind_config.destination.encode())
        basename = m.hexdigest()

        # Create the overlay workspace on tmpfs in /run
        workdir = os.path.join(volatile_root, basename)
        os.makedirs(workdir, exist_ok=True)

        overlay_upper = os.path.join(workdir, "upper")
        os.makedirs(overlay_upper, exist_ok=True)
        os.chown(overlay_upper, st.st_uid, st.st_gid)

        overlay_work = os.path.join(workdir, "work")
        os.makedirs(overlay_work, exist_ok=True)
        os.chown(overlay_work, st.st_uid, st.st_gid)

        cmd = [
            "mount",
            "-t",
            "overlay",
            "overlay",
            f"-olowerdir={bind_config.destination},upperdir={overlay_upper},workdir={overlay_work}",
            bind_config.destination,
        ]
        # logging.debug("Volatile setup command: %r", cmd)
        subprocess.run(cmd, check=True)

    @classmethod
    def bind_hook_setup_aptcache(cls, bind_config: BindConfig):
        with open("/etc/apt/apt.conf.d/99-tmp-moncic-ci-keep-downloads", "wt") as fd:
            print('Binary::apt::APT::Keep-Downloaded-Packages "1";', file=fd)
        try:
            apt_user = pwd.getpwnam("_apt")
        except KeyError:
            apt_user = None
        if apt_user:
            os.chown("/var/cache/apt/archives", apt_user.pw_uid, apt_user.pw_gid)

    @classmethod
    def bind_hook_teardown_aptcache(cls, bind_config: BindConfig):
        try:
            os.unlink("/etc/apt/apt.conf.d/99-tmp-moncic-ci-keep-downloads")
        except FileNotFoundError:
            pass

    @classmethod
    def bind_hook_setup_aptpackages(cls, bind_config: BindConfig):
        mirror_dir = os.path.dirname(bind_config.destination)
        with open(os.path.join(mirror_dir, "Packages"), "wb") as fd:
            subprocess.run(
                ["apt-ftparchive", "packages", os.path.basename(bind_config.destination)],
                cwd=mirror_dir,
                stdout=fd,
                check=True,
            )

        with open("/etc/apt/sources.list.d/tmp-moncic-ci.list", "wt") as fd:
            print(f"deb [trusted=yes] file://{mirror_dir} ./", file=fd)

        # env = dict(os.environ)
        # env.update(DEBIAN_FRONTEND="noninteractive")
        subprocess.run(apt_get_cmd("update"))
        # subprocess.run(apt_get_cmd("full-upgrade"), env=env)

    @classmethod
    def bind_hook_teardown_aptpackages(cls, bind_config: BindConfig):
        try:
            os.unlink("/etc/apt/sources.list.d/tmp-moncic-ci.list")
        except FileNotFoundError:
            pass


@dataclasses.dataclass
class ContainerConfig:
    """
    Configuration needed to customize starting a container
    """

    # If true, changes done to the container filesystem will not persist
    ephemeral: bool = True

    # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
    #
    # Leave to None to use system or container defaults.
    tmpfs: bool | None = None

    # List of bind mounts requested on the container
    binds: list[BindConfig] = dataclasses.field(default_factory=list)

    # Make sure this user exists in the container.
    # Cannot be used when ephemeral is False
    forward_user: UserConfig | None = None

    def check(self):
        """
        Raise exceptions if options are used inconsistently
        """

    def configure_workdir(self, workdir: str, bind_type="rw", mountpoint="/media"):
        """
        Configure a working directory, bind mounted into the container, set as
        the container working directory, with its user forwarded in the container.

        ``bind_type`` is passed verbatim to BindConfig.create
        """
        workdir = os.path.abspath(workdir)
        mountpoint = os.path.join(mountpoint, os.path.basename(workdir))
        self.binds.append(
            BindConfig.create(
                source=workdir,
                destination=mountpoint,
                bind_type=bind_type,
                cwd=True,
            )
        )
        self.forward_user = UserConfig.from_file(workdir)

    def run_config(self, run_config: RunConfig | None = None) -> RunConfig:
        if run_config is None:
            res = RunConfig()
        else:
            res = run_config

        # Check if there is a bind with cwd=True
        for bind in self.binds:
            if bind.cwd:
                home_bind = bind
                break
        else:
            home_bind = None

        if res.cwd is None:
            if home_bind:
                res.cwd = home_bind.destination
            elif res.user is not None and res.user.user_id != 0:
                res.cwd = f"/home/{res.user.user_name}"
            else:
                res.cwd = "/root"

        if res.user is None and home_bind:
            res.user = UserConfig.from_file(home_bind.source)

        return res


class Container(ContextManager, Protocol):
    """
    An instance of a System in execution as a container
    """

    system: System
    config: ContainerConfig
    # Default to False, set to True to leave the container running on exit
    linger: bool
    # Name of the running container instance, which can be used to access it
    # with normal user commands
    instance_name: str

    def forward_user(self, user: UserConfig, allow_maint: bool = False):
        """
        Ensure the system has a matching user and group
        """
        ...

    def get_root(self) -> Path:
        """
        Return the path to the root directory of this container
        """
        ...

    def binds(self) -> Iterator[BindConfig]:
        """
        Iterate the bind mounts active on this container
        """
        ...

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
        ...

    def run_script(self, body: str, config: RunConfig | None = None) -> CompletedCallable:
        """
        Run the given string as a script in the machine.

        A shebang at the beginning of the script will be honored.

        Returns the process exit status.
        """
        ...

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
        ...

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
        ...

    def run_shell(self, config: RunConfig | None):
        """
        Open a shell in the container
        """
        ...


class ContainerBase:
    """
    Convenience common base implementation for Container
    """

    def __init__(self, system: System, config: ContainerConfig, instance_name: str | None = None):
        global machine_name_sequence_pid, machine_name_sequence
        super().__init__()
        self.system = system
        self.linger = False

        if instance_name is None:
            current_pid = os.getpid()
            if machine_name_sequence_pid is None or machine_name_sequence_pid != current_pid:
                machine_name_sequence_pid = current_pid
                machine_name_sequence = 0

            seq = machine_name_sequence
            machine_name_sequence += 1
            self.instance_name = "mc-" + machine_name_generator(current_pid)
            if seq > 0:
                self.instance_name += str(seq)
        else:
            self.instance_name = instance_name

        config.check()
        self.config = config
        self.started = False

    def _start(self):
        raise NotImplementedError(f"{self.__class__}._start not implemented")

    def _stop(self):
        raise NotImplementedError(f"{self.__class__}._stop not implemented")

    def __enter__(self):
        if not self.started:
            try:
                self._start()
            except Exception:
                if self.started:
                    self._stop()
                raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.started and not self.linger:
            self._stop()

    def run(self, command: list[str], config: RunConfig | None = None) -> subprocess.CompletedProcess:
        raise NotImplementedError(f"{self.__class__}._run not implemented")

    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        raise NotImplementedError(f"{self.__class__}._run_callable_raw not implemented")

    def run_callable(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> Result:
        completed = self.run_callable_raw(func, config, args, kwargs)
        return completed.result()

    def run_shell(self, config: RunConfig | None):
        shell_candidates = []
        if "SHELL" in os.environ:
            shell_candidates.append(os.environ["SHELL"])
            shell_candidates.append(os.path.basename(os.environ["SHELL"]))
        shell_candidates.extend(("bash", "sh"))

        def find_shell():
            """
            lookup for a valid shell in the container
            """
            for cand in shell_candidates:
                pathname = shutil.which(cand)
                if pathname is not None:
                    return pathname
            raise RuntimeError(f"No valid shell found. Tried: {', '.join(shell_candidates)}")

        shell = self.run_callable(find_shell)
        return self.run([shell, "--login"], config=config)


class NspawnContainer(ContainerBase):
    """
    Running system implemented using systemd nspawn
    """

    def __init__(self, *args, **kw) -> None:
        super().__init__(*args, **kw)
        # machinectl properties of the running machine
        self.properties: dict[str, str] = {}
        # Bind mounts used by this container
        self.active_binds: list[BindConfig] = []

    def get_root(self) -> Path:
        return Path(self.properties["RootDirectory"])

    def binds(self) -> Iterator[BindConfig]:
        yield from self.active_binds

    def _run_nspawn(self, cmd: list[str]):
        """
        Run the given systemd-nspawn command line, contained into its own unit
        using systemd-run
        """
        unit_config = [
            "KillMode=mixed",
            "Type=notify",
            "RestartForceExitStatus=133",
            "SuccessExitStatus=133",
            "Slice=machine.slice",
            "Delegate=yes",
            "TasksMax=16384",
            "WatchdogSec=3min",
        ]

        systemd_run_cmd = ["systemd-run"]
        for c in unit_config:
            systemd_run_cmd.append(f"--property={c}")

        systemd_run_cmd.extend(cmd)

        self.system.log.info("Running %s", " ".join(shlex.quote(c) for c in systemd_run_cmd))
        res = subprocess.run(systemd_run_cmd, capture_output=True)
        if res.returncode != 0:
            self.system.log.error(
                "Failed to run %s (exit code %d): %r",
                " ".join(shlex.quote(c) for c in systemd_run_cmd),
                res.returncode,
                res.stderr,
            )
            raise RuntimeError("Failed to start container")

    def get_start_command(self):
        cmd = [
            "systemd-nspawn",
            "--quiet",
            f"--directory={self.system.path}",
            f"--machine={self.instance_name}",
            "--boot",
            "--notify-ready=yes",
            "--resolv-conf=replace-host",
        ]
        for bind_config in self.config.binds:
            self.active_binds.append(bind_config)
            cmd.append(bind_config.to_nspawn())
        if self.config.ephemeral:
            if self.config.tmpfs:
                cmd.append("--volatile=overlay")
                # See https://github.com/Truelite/nspawn-runner/issues/10
                # According to systemd-nspawn(1), --read-only is implied if --volatile
                # is used, but it seems that without using --read-only one ostree
                # remains locked and VMs can only be started once from it.
                cmd.append("--read-only")
            else:
                cmd.append("--ephemeral")
        if self.system.images.session.moncic.systemd_version >= 250:
            cmd.append("--suppress-sync=yes")
        cmd.append(f"systemd.hostname={self.instance_name}")
        return cmd

    def forward_user(self, user: UserConfig, allow_maint=False) -> None:
        """
        Ensure the system has a matching user and group
        """

        def forward():
            res = subprocess.run(["id", "-u", str(user.user_id)], capture_output=True, check=False)
            has_user = res.returncode == 0 and int(res.stdout.strip()) == user.user_id
            if not has_user and not allow_maint and not self.config.ephemeral:
                raise RuntimeError(f"user {user.user_name} not found in non-ephemeral containers")

            res = subprocess.run(["id", "-g", str(user.user_id)], capture_output=True, check=False)
            has_group = res.returncode == 0 and int(res.stdout.strip()) == user.group_id
            if not has_group and not allow_maint and not self.config.ephemeral:
                raise RuntimeError(f"user group {user.group_name} not found in non-ephemeral containers")

            if not has_user and not has_group:
                subprocess.run(["groupadd", "--gid", str(user.group_id), user.group_name], check=True)
                subprocess.run(
                    [
                        "useradd",
                        "--create-home",
                        "--uid",
                        str(user.user_id),
                        "--gid",
                        str(user.group_id),
                        user.user_name,
                    ],
                    check=True,
                )
            else:
                user.check_system()

        forward.__doc__ = f"check or create user {user.user_name!r} and group {user.group_name!r}"

        self.run_callable(forward, config=RunConfig(user=UserConfig.root()))

    def _start(self):
        self.system.log.info(
            "Starting system %s as %s using image %s", self.system.name, self.instance_name, self.system.path
        )

        cmd = self.get_start_command()

        self._run_nspawn(cmd)
        self.started = True

        # Read machine properties
        res = subprocess.run(["machinectl", "show", self.instance_name], capture_output=True, text=True, check=True)
        self.properties = {}
        for line in res.stdout.splitlines():
            key, value = line.split("=", 1)
            self.properties[key] = value

        # Do user forwarding if requested
        if self.config.forward_user:
            self.forward_user(self.config.forward_user)

        # We do not need to delete the user if it was created, because we
        # enforce that forward_user is only used on ephemeral containers

        # Set up volatile mounts
        if any(bind.setup for bind in self.active_binds):
            self.run_callable(self._bind_setup, config=RunConfig(user=UserConfig.root()))

    def _bind_setup(self):
        """
        Run setup scripts from binds
        """
        for bind in self.active_binds:
            if bind.setup:
                bind.setup(bind)

    def _bind_teardown(self):
        """
        Run teardown scripts from binds
        """
        for bind in self.active_binds:
            if bind.teardown:
                bind.teardown(bind)

    def _stop(self):
        # Run teardown script frombinds
        if any(bind.teardown for bind in self.active_binds):
            self.run_callable(self._bind_teardown, config=RunConfig(user=UserConfig.root()))

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
        self.started = False

    def run(self, command: list[str], config: RunConfig | None = None) -> CompletedCallable:
        run_config = self.config.run_config(config)

        exec_func: Callable[[str, list[str]], NoReturn]
        if run_config.use_path:
            exec_func = os.execvp
        else:
            exec_func = os.execv

        def command_runner():
            try:
                exec_func(command[0], command)
            except FileNotFoundError:
                logging.error("%r: command not found", command[0])
                # Same return code as the shell for a command not found
                return 127

        command_runner.__doc__ = " ".join(shlex.quote(c) for c in command)

        return self.run_callable_raw(command_runner, run_config)

    def run_script(self, body: str, config: RunConfig | None = None) -> CompletedCallable:
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

        return self.run_callable_raw(script_runner, config)

    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        run_config = self.config.run_config(config)
        runner = SetnsCallableRunner(self, run_config, func, args, kwargs)
        completed = runner.execute()
        return completed


class MockContainer(ContainerBase):
    """
    Mock container used for tests
    """

    def get_root(self) -> Path:
        return Path(self.properties["RootDirectory"])

    def _start(self):
        self.system.images.session.mock_log(system=self.system.name, action="container start")
        self.started = True

    def _stop(self):
        self.system.images.session.mock_log(system=self.system.name, action="container stop")
        self.started = False

    def run(self, command: list[str], config: RunConfig | None = None) -> CompletedCallable:
        run_config = self.config.run_config(config)
        self.system.images.session.mock_log(system=self.system.name, action="run", config=run_config, cmd=command)
        return self.system.images.session.get_process_result(args=command)

    def run_callable_raw(
        self,
        func: Callable[..., Result],
        config: RunConfig | None = None,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> CompletedCallable[Result]:
        run_config = self.config.run_config(config)
        self.system.images.session.mock_log(
            system=self.system.name,
            action="run callable",
            config=run_config,
            func=func.__name__,
            desc=func.__doc__,
            args=args,
            kwargs=kwargs,
        )
        return CompletedCallable(args=func.__name__, returncode=0)
