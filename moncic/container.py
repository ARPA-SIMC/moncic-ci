from __future__ import annotations

import dataclasses
import errno
import grp
import hashlib
import logging
import os
import pwd
import re
import shlex
import signal
import subprocess
import tempfile
import time
import uuid
from typing import (TYPE_CHECKING, Callable, ContextManager, Iterator, List,
                    NoReturn, Optional, Protocol)

from .nspawn import escape_bind_ro
from .runner import RunConfig, SetnsCallableRunner, UserConfig

if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)

re_split_bind = re.compile(r"(?<!\\):")


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
    mount_options: List[str] = dataclasses.field(default_factory=list)

    # If true, use this as the default working directory when running code or
    # programs in the container
    cwd: bool = False

    # Setup hook to be run at container startup inside the container
    setup: Optional[Callable[["BindConfig"], None]] = None

    # Setup hook to be run before container shutdown inside the container
    teardown: Optional[Callable[["BindConfig"], None]] = None

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
            return option + ":".join((
                escape_bind_ro(self.source),
                escape_bind_ro(self.destination),
                ",".join(self.mount_options)))
        else:
            if self.source == self.destination:
                return option + escape_bind_ro(self.source)
            else:
                return option + (escape_bind_ro(self.source) + ":" +
                                 escape_bind_ro(self.destination))

    @classmethod
    def create(cls, source: str, destination: str, bind_type: str, **kw) -> "BindConfig":
        """
        Create a BindConfig.

        ``bind_type`` has extra values over BindConfig.bind_type and can be:

        * ``ro``: read only
        * ``rw``: read-write
        * ``volatile``: readonly with an tempfs volatile overlay
        * ``aptcache``: shared /var/cache/apt/archives mount
        """
        if bind_type == "volatile":
            kw["bind_type"] = "ro"
            kw.setdefault("setup", BindConfig.bind_hook_setup_volatile)
        elif bind_type == "aptcache":
            kw["bind_type"] = "rw"
            kw.setdefault("setup", BindConfig.bind_hook_setup_aptcache)
            kw.setdefault("teardown", BindConfig.bind_hook_teardown_aptcache)
        else:
            kw["bind_type"] = bind_type

        return cls(source=source, destination=destination, **kw)

    @classmethod
    def from_nspawn(cls, entry: str, bind_type: str) -> "BindConfig":
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
            return cls.create(
                    parts[0].replace(r"\:", ":"),
                    parts[1].replace(r"\:", ":"),
                    bind_type)
        elif len(parts) == 3:
            # a colon-separated triple of source path, destination path and mount options
            return cls.create(
                    parts[0].replace(r"\:", ":"),
                    parts[1].replace(r"\:", ":"),
                    bind_type,
                    mount_options=parts[2].split(','))
        else:
            raise ValueError(f"{entry!r}: unparsable bind option")

    @classmethod
    def bind_hook_setup_volatile(cls, bind_config: "BindConfig"):
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

        cmd = ["mount", "-t", "overlay", "overlay",
               f"-olowerdir={bind_config.destination},upperdir={overlay_upper},workdir={overlay_work}",
               bind_config.destination]
        # logging.debug("Volatile setup command: %r", cmd)
        subprocess.run(cmd, check=True)

    @classmethod
    def bind_hook_setup_aptcache(cls, bind_config: "BindConfig"):
        with open("/etc/apt/apt.conf.d/99-tmp-moncic-ci-keep-downloads", "wt") as fd:
            print('Binary::apt::APT::Keep-Downloaded-Packages "1";', file=fd)
        try:
            apt_user = pwd.getpwnam("_apt")
        except KeyError:
            apt_user = None
        if apt_user:
            os.chown("/var/cache/apt/archives", apt_user.pw_uid, apt_user.pw_gid)

    @classmethod
    def bind_hook_teardown_aptcache(cls, bind_config: "BindConfig"):
        try:
            os.unlink("/etc/apt/apt.conf.d/99-tmp-moncic-ci-keep-downloads")
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
    tmpfs: Optional[bool] = None

    # List of bind mounts requested on the container
    binds: List[BindConfig] = dataclasses.field(default_factory=list)

    # Make sure this user exists in the container.
    # Cannot be used when ephemeral is False
    forward_user: Optional[UserConfig] = None

    def check(self):
        """
        Raise exceptions if options are used inconsistently
        """
        pass

    def configure_workdir(self, workdir: str, bind_type="rw"):
        """
        Configure a working directory, bind mounted into the container, set as
        the container working directory, with its user forwarded in the container.

        ``bind_type`` is passed verbatim to BindConfig.create
        """
        workdir = os.path.abspath(workdir)
        mountpoint = os.path.join("/media", os.path.basename(workdir))
        self.binds.append(BindConfig.create(
            source=workdir,
            destination=mountpoint,
            bind_type=bind_type,
            cwd=True,
        ))
        self.forward_user = UserConfig.from_file(workdir)

    def run_config(self, run_config: Optional[RunConfig] = None) -> RunConfig:
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

    def forward_user(self, user: UserConfig, allow_maint: bool = False):
        """
        Ensure the system has a matching user and group
        """
        ...

    def get_root(self) -> str:
        """
        Return the path to the root directory of this container
        """
        ...

    def binds(self) -> Iterator[BindConfig]:
        """
        Iterate the bind mounts active on this container
        """
        ...

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


class ContainerBase:
    """
    Convenience common base implementation for Container
    """
    def __init__(self, system: System, config: ContainerConfig, instance_name: Optional[str] = None):
        super().__init__()
        self.system = system

        if instance_name is None:
            self.instance_name = str(uuid.uuid4())
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
        if self.started:
            self._stop()


class NspawnContainer(ContainerBase):
    """
    Running system implemented using systemd nspawn
    """
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        # machinectl properties of the running machine
        self.properties = None
        # Bind mounts used by this container
        self.active_binds: List[BindConfig] = []

    def get_root(self) -> str:
        return self.properties["RootDirectory"]

    def binds(self) -> Iterator[BindConfig]:
        yield from self.active_binds

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
        return cmd

    def forward_user(self, user: UserConfig, allow_maint=False):
        """
        Ensure the system has a matching user and group
        """
        def forward():
            try:
                pw = pwd.getpwuid(user.user_id)
            except KeyError:
                pw = None
                if not allow_maint and not self.config.ephemeral:
                    raise RuntimeError(f"user {user.user_name} not found in non-ephemeral containers")

            try:
                gr = grp.getgrgid(user.group_id)
            except KeyError:
                gr = None
                if not allow_maint and not self.config.ephemeral:
                    raise RuntimeError(f"user group {user.group_name} not found in non-ephemeral containers")

            if pw is None and gr is None:
                subprocess.run([
                    "groupadd",
                    "--gid", str(user.group_id),
                    user.group_name], check=True)
                subprocess.run([
                    "useradd",
                    "--create-home",
                    "--uid", str(user.user_id),
                    "--gid", str(user.group_id),
                    user.user_name], check=True)
            else:
                user.check_system()
        forward.__doc__ = f"check or create user {user.user_name!r} and group {user.group_name!r}"

        self.run_callable(forward, config=RunConfig(user=UserConfig.root()))

    def _start(self):
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
        Finish setting up volatile binds in the container
        """
        for bind in self.active_binds:
            if bind.setup:
                bind.setup(bind)

    def _bind_teardown(self):
        """
        Run cleanup script from binds
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

    def run(self, command: List[str], config: Optional[RunConfig] = None) -> subprocess.CompletedProcess:
        run_config = self.config.run_config(config)

        exec_func: Callable[[str, List[str]], NoReturn]
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

        return self.run_callable(command_runner, run_config)

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
