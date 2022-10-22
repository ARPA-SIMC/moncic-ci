from __future__ import annotations

import dataclasses
import logging
import os
import shlex
import shutil
import subprocess
from typing import TYPE_CHECKING, Dict, List, Optional, TextIO, Type

from .container import ContainerConfig
from .runner import UserConfig
from . import distro

if TYPE_CHECKING:
    from .container import Container, System

log = logging.getLogger(__name__)


def run(cmd, check=True, **kwargs):
    """
    subprocess.run wrapper that has check=True by default and logs the commands
    run
    """
    log.info("Run: %s", " ".join(shlex.quote(c) for c in cmd))
    return subprocess.run(cmd, check=check, **kwargs)


def link_or_copy(src: str, dstdir: str, filename: Optional[str] = None, user: Optional[UserConfig] = None):
    """
    Try to make a hardlink of src inside directory dstdir.

    If hardlinking is not possible, copy it
    """
    if filename is None:
        filename = os.path.basename(src)
    dest = os.path.join(dstdir, filename)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)

    if user is not None:
        os.chown(dest, user.user_id, user.group_id)


class Builder:
    """
    Interface for classes providing the logic for CI builds
    """
    # Registry of known builders
    builders: Dict[str, Type[Builder]] = {}

    @classmethod
    def register(cls, builder_cls: Type["Builder"]) -> Type["Builder"]:
        name = getattr(builder_cls, "NAME", None)
        if name is None:
            name = builder_cls.__name__.lower()
        cls.builders[name] = builder_cls
        return builder_cls

    @classmethod
    def list(cls) -> List[str]:
        return list(cls.builders.keys())

    @classmethod
    def create_builder(cls, name: str, system: System, srcdir: str) -> "Builder":
        builder_cls = cls.builders[name.lower()]
        return builder_cls.create(system, srcdir)

    @classmethod
    def create(cls, system: System, srcdir: str) -> "Builder":
        raise NotImplementedError(f"The builder {cls} cannot be instantiated")

    @classmethod
    def detect(cls, system: System, srcdir: str) -> "Builder":
        if isinstance(system.distro, distro.DebianDistro):
            return cls.builders["debian"].create(system, srcdir)
        elif isinstance(system.distro, distro.RpmDistro):
            return cls.builders["rpm"].create(system, srcdir)
        else:
            raise NotImplementedError(f"No suitable builder found for distribution {system.distro!r}")

    def __init__(self, system: System, srcdir: str):
        """
        The constructor is run in the host system
        """
        # System used for the build
        self.system = system
        # Directory where sources are found in the host system
        self.srcdir = srcdir
        # User to use for the build
        self.user = UserConfig.from_sudoer()
        # Build log file
        self.buildlog_file: Optional[TextIO] = None
        # Log handler used to capture build output
        self.buildlog_handler: Optional[logging.Handler] = None

    def setup_container_host(self, container: Container):
        """
        Set up the container before starting the build.

        This is run on the host system before starting the build
        """
        container_root = container.get_root()

        # Set user permissions on source and build directories
        srcdir = os.path.join(container_root, "srv", "moncic-ci", "source")
        os.chown(srcdir, self.user.user_id, self.user.group_id)
        builddir = os.path.join(container_root, "srv", "moncic-ci", "build")
        os.makedirs(builddir, exist_ok=True)
        os.chown(builddir, self.user.user_id, self.user.group_id)

        # Capture build log
        log_file = os.path.join(container_root, "srv", "moncic-ci", "buildlog")
        self.log_capture_start(log_file)

    def setup_container_guest(self):
        """
        Set up the build environment in the container
        """
        # Reinstantiate the module logger
        global log
        log = logging.getLogger(__name__)

    def log_capture_start(self, log_file: str):
        self.buildlog_file = open(log_file, "wt")
        self.buildlog_handler = logging.StreamHandler(self.buildlog_file)
        self.buildlog_handler.setLevel(logging.DEBUG)
        self.buildlog_handler.setFormatter(
                logging.Formatter("%(asctime)-19.19s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(self.buildlog_handler)
        logging.getLogger().setLevel(logging.DEBUG)

    def log_capture_end(self):
        if self.buildlog_handler is not None:
            logging.getLogger().removeHandler(self.buildlog_handler)
            self.buildlog_handler = None
            self.buildlog_file.close()
            self.buildlog_file = None

    def build(self, shell: bool = False, source_only: bool = False) -> int:
        """
        Run the build, store the artifacts in the given directory if requested,
        return the returncode of the build process
        """
        artifacts_dir = self.system.images.session.moncic.config.build_artifacts_dir
        container_config = ContainerConfig()
        # Mount the source directory as /srv/moncic-ci/source/<name>
        # Set it as the default current directory in the container
        # Mounted volatile to prevent changes to it
        container_config.configure_workdir(self.srcdir, bind_type="volatile", mountpoint="/srv/moncic-ci/source")
        container = self.system.create_container(config=container_config)
        with container:
            self.setup_container_host(container)

            # Log moncic config
            moncic_config = self.system.images.session.moncic.config
            for field in dataclasses.fields(moncic_config):
                log.debug("moncic:%s = %r", field.name, getattr(moncic_config, field.name))
            # Log container config
            for field in dataclasses.fields(container_config):
                log.debug("container:%s = %r", field.name, getattr(container_config, field.name))

            try:
                # Build run config
                run_config = container_config.run_config()
                run_config.user = UserConfig.root()
                # Log run config
                for field in dataclasses.fields(run_config):
                    log.debug("run:%s = %r", field.name, getattr(run_config, field.name))

                try:
                    res = container.run_callable(
                            self.build_in_container,
                            run_config,
                            kwargs={"source_only": source_only})
                    if artifacts_dir:
                        self.collect_artifacts(container, artifacts_dir)
                finally:
                    if shell:
                        run_config = container_config.run_config()
                        run_config.interactive = True
                        run_config.check = False
                        run_config.user = UserConfig.root()
                        run_config.cwd = "/srv/moncic-ci/build"
                        container.run_shell(config=run_config)
            finally:
                self.log_capture_end()
        return res.returncode

    def build_in_container(self, source_only: bool = False) -> Optional[int]:
        """
        Run the build in a child process.

        The function will be callsed inside the running system.

        The current directory will be set to the source directory in /srv/moncic-ci/source/<name>.

        Standard output and standard error are logged.

        The return value will be used as the return code of the child process.
        """
        raise NotImplementedError(f"{self.__class__}.build not implemented")

    def collect_artifacts(self, container: Container, destdir: str):
        """
        Copy build artifacts to the given directory
        """
        # Do nothing by default
        pass
