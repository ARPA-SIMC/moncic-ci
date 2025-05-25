import logging
from pathlib import Path

from moncic.runner import RunConfig, UserConfig

from .binds import BindConfig, BindType


class ContainerConfig:
    """
    Configuration needed to customize starting a container
    """

    def __init__(self) -> None:
        #: If true, changes done to the container filesystem will not persist
        self.ephemeral: bool = True

        #: Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
        #
        # Leave to None to use system or container defaults.
        self.tmpfs: bool | None = None

        #: List of bind mounts requested on the container
        self.binds: list[BindConfig] = []

        #: Make sure this user exists in the container.
        #:  Cannot be used when ephemeral is False
        self.forward_user: UserConfig | None = None

    def log_debug(self, logger: logging.Logger) -> None:
        logger.debug("container:ephemeral = %r", self.ephemeral)
        logger.debug("container:tmpfs = %r", self.tmpfs)
        logger.debug("container:forward_user = %r", self.forward_user)
        for bind in self.binds:
            logger.debug(
                "container:bind: type=%s host=%s guest=%s cwd=%s",
                bind.bind_type,
                bind.source,
                bind.destination,
                bind.cwd,
            )

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
