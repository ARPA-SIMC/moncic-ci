from __future__ import annotations

import abc
import dataclasses
import hashlib
import logging
import io
import os
import pwd
import re
import subprocess
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ContextManager, TypeVar
from collections.abc import Callable

from .image import Image
from .runner import CompletedCallable, RunConfig, UserConfig
from .utils import libbanana
from .utils.deb import apt_get_cmd
from .utils.script import Script
from .utils.nspawn import escape_bind_ro

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

    # Type of bind mount (see create())
    bind_type: str = "rw"

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
        match self.bind_type:
            case "ro":
                option = "--bind-ro="
            case "rw":
                option = "--bind="
            case _:
                raise ValueError(f"{self.bind_type!r}: invalid bind type")

        if self.source == self.destination:
            return option + escape_bind_ro(self.source)
        else:
            return option + (escape_bind_ro(self.source) + ":" + escape_bind_ro(self.destination))

    def to_podman(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "Type": "bind",
            "Source": self.source,
            "Target": self.destination,
        }
        match self.bind_type:
            case "ro":
                res["Readonly"] = "true"
            case "rw":
                res["Readonly"] = "false"
            case _:
                raise ValueError(f"{self.bind_type!r}: invalid bind type")
        return res

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
            return cls.create(parts[0].replace(r"\:", ":"), parts[1].replace(r"\:", ":"), bind_type)
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
        with open("/etc/apt/apt.conf.d/99-tmp-moncic-ci-keep-downloads", "w") as fd:
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

        with open("/etc/apt/sources.list.d/tmp-moncic-ci.list", "w") as fd:
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


class Container(ContextManager, abc.ABC):
    """
    An instance of an Image in execution as a container
    """

    def __init__(self, image: Image, *, config: ContainerConfig, instance_name: str | None = None):
        super().__init__()
        config.check()
        self.image = image
        self.config = config
        self.started = False
        #: Default to False, set to True to leave the container running on exit
        self.linger: bool = False
        #: User-provided instance name
        self._instance_name = instance_name
        self.startup_script = Script("Container startup script")
        self.teardown_script = Script("Container teardown script")

    @property
    def instance_name(self) -> str:
        """
        Name of the running container instance, which can be used to access it
        with normal user commands
        """
        if self._instance_name:
            return self._instance_name
        return self.get_instance_name()

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
        if not self.started:
            try:
                self._start()
            except Exception:
                if self.started:
                    self._stop()
                raise
            self._post_start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.started and not self.linger:
            self._pre_stop()
            self._stop(exc_val)

    @abc.abstractmethod
    def _start(self) -> None:
        """Start the container."""

    @abc.abstractmethod
    def _stop(self, exc: Exception | None = None) -> None:
        """Stop the container."""

    def _post_start(self) -> None:
        """Run post-start container configuration."""
        if self.startup_script:
            with io.StringIO() as buf:
                self.startup_script.print(file=buf)
                self.run_script(buf.getvalue(), RunConfig(check=True, cwd="/"))

    def _pre_stop(self) -> None:
        """Run pre-stop container teardown."""
        if self.teardown_script:
            with io.StringIO() as buf:
                self.teardown_script.print(file=buf)
                self.run_script(buf.getvalue(), RunConfig(check=True, cwd="/"))

    @abc.abstractmethod
    def forward_user(self, user: UserConfig, allow_maint: bool = False):
        """
        Ensure the system has a matching user and group
        """

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

    @abc.abstractmethod
    def run_script(self, body: str, config: RunConfig | None = None) -> CompletedCallable:
        """
        Run the given string as a script in the machine.

        A shebang at the beginning of the script will be honored.

        Returns the process exit status.
        """

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
