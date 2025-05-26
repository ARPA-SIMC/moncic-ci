import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Generator, ContextManager, Callable

from moncic.runner import RunConfig, UserConfig
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
