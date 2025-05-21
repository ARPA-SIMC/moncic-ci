from __future__ import annotations

import abc
import dataclasses
import enum
import hashlib
import logging
import io
import os
import re
import shlex
import tempfile
from collections.abc import Iterator, Generator
from contextlib import ExitStack, contextmanager
from functools import cached_property
from pathlib import Path
from typing import Any, TypeVar, assert_never, TypedDict, override, ContextManager
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


class BindType(enum.StrEnum):
    """Available bind types."""

    READONLY = "ro"
    READWRITE = "rw"
    VOLATILE = "volatile"
    APTCACHE = "aptcache"
    APTPACKAGES = "aptpackages"


class BindConfig(abc.ABC):
    """
    Configuration of one bind mount requested on the container
    """

    def __init__(self, bind_type: BindType, source: Path, destination: Path, cwd: bool = False) -> None:
        # Type of bind mount
        self.bind_type = bind_type
        # Directory in the host system to be bind mounted in the container
        #
        # The source path may optionally be prefixed with a "+" character. If
        # so, the source path is taken relative to the image's root directory.
        # This permits setting up bind mounts within the container image.
        #
        # The source path may be specified as empty string, in which case a
        # temporary directory below the host's /var/tmp/ directory is used.
        # It is automatically removed when the container is shut down.
        self.source = source
        # Directory inside the container where the directory gets bind mounted
        self.destination = destination
        # If true, use this as the default working directory when running code or
        # programs in the container
        self.cwd = cwd

    @classmethod
    def create(
        cls, source: str | Path, destination: str | Path, bind_type: str | BindType, cwd: bool = False
    ) -> "BindConfig":
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

        Args = TypedDict("Args", {"source": Path, "destination": Path, "cwd": bool})
        args: Args = {"source": Path(source), "destination": Path(destination), "cwd": cwd}

        match BindType(bind_type):
            case BindType.READONLY:
                return BindConfigReadonly(**args)
            case BindType.READWRITE:
                return BindConfigReadwrite(**args)
            case BindType.VOLATILE:
                return BindConfigVolatile(**args)
            case BindType.APTCACHE:
                return BindConfigAptCache(**args)
            case BindType.APTPACKAGES:
                return BindConfigAptPackages(**args)
            case _ as unreachable:
                assert_never(unreachable)

    @classmethod
    def from_nspawn(cls, entry: str, bind_type: str | BindType) -> "BindConfig":
        """
        Create a BindConfig from an nspawn --bind/--bind-ro option.

        ``bind_type`` is passed verbatim to BindConfig.create
        """
        # Backslash escapes are interpreted, so "\:" may be used to embed colons in either path.
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

    @abc.abstractmethod
    def to_nspawn(self) -> str:
        """Return the nspawn --bind* option for this bind."""

    @abc.abstractmethod
    def to_podman(self) -> dict[str, Any]:
        """Return the podman mount object for this bind."""

    @contextmanager
    def setup(self, container: "Container") -> Generator[None, None, None]:
        """Set up this bind after the container has started."""
        yield None

    def _run_script(self, script: Script, container: "Container") -> None:
        """Run the setup/teardown script in the container."""
        if not script:
            return
        with io.StringIO() as buf:
            script.print(file=buf)
            container.run_script(buf.getvalue(), RunConfig(check=True, cwd=Path("/"), user=UserConfig.root()))


class BindConfigReadonly(BindConfig):
    """Readonly bind mount."""

    def __init__(self, source: Path, destination: Path, cwd: bool = False) -> None:
        super().__init__(bind_type=BindType.READONLY, source=source, destination=destination, cwd=cwd)

    @override
    def to_nspawn(self) -> str:
        option = "--bind-ro="
        if self.source == self.destination:
            return option + escape_bind_ro(self.source)
        else:
            return option + (escape_bind_ro(self.source) + ":" + escape_bind_ro(self.destination))

    @override
    def to_podman(self) -> dict[str, Any]:
        return {
            "Type": "bind",
            "Readonly": "true",
            "Source": self.source.as_posix(),
            "Target": self.destination.as_posix(),
        }


class BindConfigReadwrite(BindConfig):
    """Read-write bind mount."""

    def __init__(self, source: Path, destination: Path, cwd: bool = False) -> None:
        super().__init__(bind_type=BindType.READWRITE, source=source, destination=destination, cwd=cwd)

    @override
    def to_nspawn(self) -> str:
        option = "--bind="
        if self.source == self.destination:
            return option + escape_bind_ro(self.source)
        else:
            return option + (escape_bind_ro(self.source) + ":" + escape_bind_ro(self.destination))

    @override
    def to_podman(self) -> dict[str, Any]:
        return {
            "Type": "bind",
            "Readonly": "false",
            "Source": self.source.as_posix(),
            "Target": self.destination.as_posix(),
        }


class BindConfigVolatile(BindConfig):
    """Readonly bind mount with a volatile overlay."""

    def __init__(self, source: Path, destination: Path, cwd: bool = False) -> None:
        super().__init__(bind_type=BindType.VOLATILE, source=source, destination=destination, cwd=cwd)

    @override
    def to_nspawn(self) -> str:
        option = "--bind-ro="
        return option + (escape_bind_ro(self.source) + ":" + escape_bind_ro(Path(f"{self.destination}-readonly")))

    @override
    def to_podman(self) -> dict[str, Any]:
        return {
            "Type": "bind",
            "Source": self.source.as_posix(),
            "Target": f"{self.destination}-readonly",
            "Readonly": "true",
        }

    @override
    @contextmanager
    def setup(self, container: "Container") -> Generator[None, None, None]:
        volatile_root = Path("/run/volatile")
        volatile_readonly_base = Path(f"{self.destination}-readonly")

        # Create the overlay workspace on tmpfs in /run
        m = hashlib.sha1()
        m.update(self.destination.as_posix().encode())
        workdir = volatile_root / m.hexdigest()

        script = Script(f"Volatile mount setup for {self.destination}")

        script.run(["mkdir", "-p", self.destination.as_posix()])

        overlay_upper = workdir / "upper"
        script.run(["mkdir", "-p", overlay_upper.as_posix()])
        script.run(["chown", "--reference=" + volatile_readonly_base.as_posix(), overlay_upper.as_posix()])

        overlay_work = workdir / "work"
        script.run(["mkdir", "-p", overlay_work.as_posix()])
        script.run(["chown", "--reference=" + volatile_readonly_base.as_posix(), overlay_work.as_posix()])

        script.run(
            [
                "mount",
                "-t",
                "overlay",
                "overlay",
                f"-olowerdir={volatile_readonly_base},upperdir={overlay_upper},workdir={overlay_work}",
                self.destination.as_posix(),
            ]
        )

        self._run_script(script, container)
        yield None


class BindConfigAptCache(BindConfig):
    """APT cache directory with packages preserved across runs."""

    def __init__(self, source: Path, destination: Path, cwd: bool = False) -> None:
        super().__init__(bind_type=BindType.APTCACHE, source=source, destination=destination, cwd=cwd)

    @override
    def to_nspawn(self) -> str:
        option = "--bind="
        if self.source == self.destination:
            return option + escape_bind_ro(self.source)
        else:
            return option + (escape_bind_ro(self.source) + ":" + escape_bind_ro(self.destination))

    @override
    def to_podman(self) -> dict[str, Any]:
        return {
            "Type": "bind",
            "Readonly": "false",
            "Source": self.source.as_posix(),
            "Target": self.destination.as_posix(),
        }

    @override
    @contextmanager
    def setup(self, container: "Container") -> Generator[None, None, None]:
        setup_script = Script(f"apt cache mount setup for {self.destination}")
        setup_script.write(
            Path("/etc/apt/apt.conf.d/99-tmp-moncic-ci-keep-downloads"),
            'Binary::apt::APT::Keep-Downloaded-Packages "1";',
            description="Do not clear apt cache",
        )
        # TODO:
        # try:
        #     apt_user = pwd.getpwnam("_apt")
        # except KeyError:
        #     apt_user = None
        # if apt_user:
        #     os.chown("/var/cache/apt/archives", apt_user.pw_uid, apt_user.pw_gid)

        teardown_script = Script(f"apt cache mount teardown for {self.destination}")
        teardown_script.run(["rm", "-f", "/etc/apt/apt.conf.d/99-tmp-moncic-ci-keep-downloads"])

        self._run_script(setup_script, container)
        try:
            yield None
        finally:
            self._run_script(teardown_script, container)


class BindConfigAptPackages(BindConfig):
    """APT package source."""

    def __init__(self, source: Path, destination: Path, cwd: bool = False) -> None:
        super().__init__(bind_type=BindType.APTPACKAGES, source=source, destination=destination, cwd=cwd)

    @override
    def to_nspawn(self) -> str:
        option = "--bind-ro="
        if self.source == self.destination:
            return option + escape_bind_ro(self.source)
        else:
            return option + (escape_bind_ro(self.source) + ":" + escape_bind_ro(self.destination))

    @override
    def to_podman(self) -> dict[str, Any]:
        return {
            "Type": "bind",
            "Readonly": "true",
            "Source": self.source.as_posix(),
            "Target": self.destination.as_posix(),
        }

    @override
    @contextmanager
    def setup(self, container: "Container") -> Generator[None, None, None]:
        mirror_dir = self.destination.parent
        packages_file = mirror_dir / "Packages"

        setup_script = Script(f"apt packages mount setup for {self.destination}")
        setup_script.run(["apt-ftparchive", "packages", mirror_dir.name], output=packages_file, cwd=mirror_dir)
        setup_script.write(
            Path("/etc/apt/sources.list.d/tmp-moncic-ci.list"), f"deb [trusted=yes] file://{mirror_dir} ./"
        )

        # env = dict(os.environ)
        # env.update(DEBIAN_FRONTEND="noninteractive")
        setup_script.run(apt_get_cmd("update"))
        # subprocess.run(apt_get_cmd("full-upgrade"), env=env)

        teardown_script = Script(f"apt packages mount teardown for {self.destination}")
        teardown_script.run(["rm", "-f", "/etc/apt/sources.list.d/tmp-moncic-ci.list"])
        teardown_script.run(["rm", "-f", packages_file.as_posix()])

        self._run_script(setup_script, container)
        try:
            yield None
        finally:
            self._run_script(teardown_script, container)


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

    def configure_workdir(self, workdir: Path, bind_type: str = "rw", mountpoint: Path = Path("/media")):
        """
        Configure a working directory, bind mounted into the container, set as
        the container working directory, with its user forwarded in the container.

        ``bind_type`` is passed verbatim to BindConfig.create
        """
        workdir = workdir.absolute()
        mountpoint = mountpoint / workdir.name
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
                res.cwd = Path(f"/home/{res.user.user_name}")
            else:
                res.cwd = Path("/root")

        if res.user is None and home_bind:
            res.user = UserConfig.from_file(home_bind.source)

        return res


class Container(abc.ABC):
    """
    An instance of an Image in execution as a container
    """

    def __init__(self, image: Image, *, config: ContainerConfig, instance_name: str | None = None):
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

    @cached_property
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
        self.stack.__enter__()
        self.stack.enter_context(self._container())
        for bind in self.config.binds:
            self.stack.enter_context(bind.setup(self))
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
    def run_script(self, body: str | Script, config: RunConfig | None = None) -> CompletedCallable:
        """
        Run the given Script or string as a script in the machine.

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
