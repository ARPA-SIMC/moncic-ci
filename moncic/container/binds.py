import abc
import enum
import hashlib
import os
import re
import shlex
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, assert_never, override

from moncic.image import ImageType
from moncic.utils.deb import apt_get_cmd
from moncic.utils.nspawn import escape_bind_ro
from moncic.utils.script import Script

if TYPE_CHECKING:
    from .container import Container

re_split_bind = re.compile(r"(?<!\\):")


class BindType(enum.StrEnum):
    """Available bind types."""

    READONLY = "ro"
    READWRITE = "rw"
    VOLATILE = "volatile"
    APTCACHE = "aptcache"
    APTPACKAGES = "aptpackages"
    ARTIFACTS = "artifacts"


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

        class Args(TypedDict):
            source: Path
            destination: Path
            cwd: bool

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
            case BindType.ARTIFACTS:
                return BindConfigArtifacts(**args)
            case _ as unreachable:
                assert_never(unreachable)

    @classmethod
    def from_nspawn(cls, entry: str, bind_type: BindType) -> "BindConfig":
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
    def host_setup(self, container: "Container") -> Generator[None, None, None]:
        """Set up this bind before the container has started."""
        yield None

    @contextmanager
    def guest_setup(self, container: "Container") -> Generator[None, None, None]:
        """Set up this bind after the container has started."""
        yield None

    def _run_script(self, script: Script, container: "Container") -> None:
        """Run the setup/teardown script in the container."""
        if not script:
            return
        container.run_script(script)


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
        self.overlay: Path | None = None

    @override
    def to_nspawn(self) -> str:
        return f"--bind={escape_bind_ro(self.source)}:{escape_bind_ro(self.destination)}-readonly"

    @override
    def to_podman(self) -> dict[str, Any]:
        assert self.overlay is not None
        return {
            "Type": "bind",
            "Source": self.overlay.as_posix(),
            "Target": self.destination.as_posix(),
            "Readonly": "false",
        }

    @override
    @contextmanager
    def host_setup(self, container: "Container") -> Generator[None, None, None]:
        if container.image.image_type != ImageType.PODMAN:
            yield None
            return
        rundir = Path(f"/var/run/user/{os.getuid()}/moncic-ci")
        rundir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=rundir) as workdir_str:
            workdir = Path(workdir_str)
            upper = workdir / "upper"
            upper.mkdir()
            work = workdir / "work"
            work.mkdir()
            overlay = workdir / "overlay"
            overlay.mkdir()
            cmd = [
                "fuse-overlayfs",
                "-o",
                (
                    f"lowerdir={shlex.quote(self.source.as_posix())},"
                    f"upperdir={shlex.quote(upper.as_posix())},"
                    f"workdir={shlex.quote(work.as_posix())}"
                ),
                overlay.as_posix(),
            ]
            print(shlex.join(cmd))
            subprocess.run(cmd, check=True)
            self.overlay = overlay
            try:
                yield
            finally:
                subprocess.run(
                    ["umount", overlay.as_posix()],
                    check=True,
                )
                self.overlay = None

    @override
    @contextmanager
    def guest_setup(self, container: "Container") -> Generator[None, None, None]:
        if container.image.image_type != ImageType.NSPAWN:
            yield None
            return
        volatile_root = Path("/run/volatile")
        volatile_readonly_base = Path(f"{self.destination}-readonly")

        # Create the overlay workspace on tmpfs in /run
        m = hashlib.sha1()
        m.update(self.destination.as_posix().encode())
        workdir = volatile_root / m.hexdigest()

        script = Script(f"Volatile mount setup for {self.destination}", cwd=Path("/"), root=True)

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
    def guest_setup(self, container: "Container") -> Generator[None, None, None]:
        # Hand over the apt package permissions to the _apt user (if present),
        # and then back to the invoking user on return
        setup_script = Script(f"apt cache mount setup for {self.destination}", cwd=Path("/"), root=True)
        setup_script.write(
            Path("/etc/apt/apt.conf.d/99-tmp-moncic-ci-keep-downloads"),
            'Binary::apt::APT::Keep-Downloaded-Packages "1";',
            description="Do not clear apt cache",
        )
        with setup_script.if_("id -u _apt > /dev/null"):
            setup_script.run(["touch", "/var/cache/apt/archives/.moncic-ci"])
            setup_script.run(["chown", "--reference=/var/cache/apt/archives", "/var/cache/apt/archives/.moncic-ci"])
            setup_script.run_unquoted("chown _apt /var/cache/apt/archives/*.deb")
            setup_script.run(["chown", "_apt", "/var/cache/apt/archives"])

        teardown_script = Script(f"apt cache mount teardown for {self.destination}", cwd=Path("/"), root=True)
        teardown_script.run(["rm", "-f", "/etc/apt/apt.conf.d/99-tmp-moncic-ci-keep-downloads"])
        teardown_script.run(
            ["chown", "-R", "--reference=/var/cache/apt/archives/.moncic-ci", "/var/cache/apt/archives"]
        )

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
    def guest_setup(self, container: "Container") -> Generator[None, None, None]:
        mirror_dir = self.destination.parent
        packages_file = mirror_dir / "Packages"

        setup_script = Script(f"apt packages mount setup for {self.destination}", cwd=Path("/"), root=True)
        setup_script.run(["apt-ftparchive", "packages", mirror_dir.name], output=packages_file, cwd=mirror_dir)
        setup_script.write(
            Path("/etc/apt/sources.list.d/tmp-moncic-ci.list"), f"deb [trusted=yes] file://{mirror_dir} ./"
        )

        # env = dict(os.environ)
        # env.update(DEBIAN_FRONTEND="noninteractive")
        setup_script.run(apt_get_cmd("update"))
        # subprocess.run(apt_get_cmd("full-upgrade"), env=env)

        teardown_script = Script(f"apt packages mount teardown for {self.destination}", cwd=Path("/"), root=True)
        teardown_script.run(["rm", "-f", "/etc/apt/sources.list.d/tmp-moncic-ci.list"])
        teardown_script.run(["rm", "-f", packages_file.as_posix()])

        self._run_script(setup_script, container)
        try:
            yield None
        finally:
            self._run_script(teardown_script, container)


class BindConfigArtifacts(BindConfig):
    """Directory that can be used to collect build artifacts."""

    def __init__(self, source: Path, destination: Path, cwd: bool = False) -> None:
        super().__init__(bind_type=BindType.ARTIFACTS, source=source, destination=destination, cwd=cwd)

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
    def guest_setup(self, container: "Container") -> Generator[None, None, None]:
        teardown_script = Script(f"Artifacts mount teardown for {self.destination}", cwd=Path("/"), root=True)
        teardown_script.run(["chown", "-R", f"--reference={self.destination}", self.destination.as_posix()])
        try:
            yield None
        finally:
            self._run_script(teardown_script, container)
