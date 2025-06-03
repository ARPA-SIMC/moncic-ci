import contextlib
import dataclasses
import logging
from collections.abc import Callable, Generator
from pathlib import Path
from typing import TYPE_CHECKING, ContextManager

from moncic.runner import UserConfig
from moncic.utils.script import Script

from .binds import BindConfig, BindType

if TYPE_CHECKING:
    from .container import Container


class ContainerConfig:
    """
    Configuration needed to customize starting a container
    """

    def __init__(self) -> None:
        #: List of bind mounts requested on the container
        self.binds: list[BindConfig] = []

        #: Make sure this user exists in the container.
        #:  Cannot be used when ephemeral is False
        self.forward_user: UserConfig | None = None

        #: Hooks to run before the contaner is started / after it has stopped
        self.host_setup_hooks: list[Callable[["Container"], ContextManager[None]]] = []
        #: Hooks to run after the contaner is started / before it has stopped
        self.guest_setup_hooks: list[Callable[["Container"], ContextManager[None]]] = []

    def add_guest_scripts(self, *, setup: Script | None = None, teardown: Script | None = None) -> None:
        """Schedule scripts to be run in the container after starting/before stopping."""

        @contextlib.contextmanager
        def hook(container: "Container") -> Generator[None, None, None]:
            if setup:
                container.run_script(setup)
            try:
                yield None
            finally:
                if teardown:
                    container.run_script(teardown)

        self.guest_setup_hooks.append(hook)

    def log_debug(self, logger: logging.Logger) -> None:
        logger.debug("container:forward_user = %r", self.forward_user)
        for bind in self.binds:
            logger.debug(
                "container:bind: type=%s host=%s guest=%s cwd=%s",
                bind.bind_type,
                bind.source,
                bind.destination,
                bind.cwd,
            )

    @contextlib.contextmanager
    def host_setup(self, container: "Container") -> Generator[None, None, None]:
        """Perform setup/teardown on the host before starting/after stopping the container."""
        with contextlib.ExitStack() as stack:
            for bind in self.binds:
                stack.enter_context(bind.host_setup(container))
            for hook in self.host_setup_hooks:
                stack.enter_context(hook(container))
            yield None

    @contextlib.contextmanager
    def guest_setup(self, container: "Container") -> Generator[None, None, None]:
        """Perform setup/teardown on the guest after starting/before stopping the container."""
        with contextlib.ExitStack() as stack:
            for bind in self.binds:
                stack.enter_context(bind.guest_setup(container))
            for hook in self.guest_setup_hooks:
                stack.enter_context(hook(container))
            yield None

    def add_bind(self, source: Path, destination: Path, bind_type: BindType, cwd: bool = False) -> None:
        """Add a bind to this container configuration."""
        self.binds.append(BindConfig.create(source, destination, bind_type, cwd))

    def check(self) -> None:
        """
        Raise exceptions if options are used inconsistently
        """

    def configure_workdir(
        self, workdir: Path, bind_type: BindType = BindType.READWRITE, mountpoint: Path = Path("/media")
    ) -> None:
        """
        Configure a working directory, bind mounted into the container, set as
        the container working directory, with its user forwarded in the container.

        ``bind_type`` is passed verbatim to BindConfig.create
        """
        workdir = workdir.absolute()
        mountpoint = mountpoint / workdir.name
        self.add_bind(
            source=workdir,
            destination=mountpoint,
            bind_type=bind_type,
            cwd=True,
        )
        self.forward_user = UserConfig.from_file(workdir)

    def get_default_cwd(self) -> Path | None:
        """Get the default working directory for this container."""
        # Check if there is a bind with cwd=True
        for bind in self.binds:
            if bind.cwd:
                return bind.destination
        return None

    def get_default_user(self) -> UserConfig | None:
        """Get the default user for this container."""
        for bind in self.binds:
            if bind.cwd:
                return UserConfig.from_file(bind.source)
        return None


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

    # Run with networking disabled
    disable_network: bool = False
