from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import (TYPE_CHECKING, Any, Callable, Dict, List, Optional, TextIO,
                    Type)

from ..container import ContainerConfig
from ..runner import UserConfig
from ..utils.guest import guest_only, host_only
from .analyze import Analyzer

if TYPE_CHECKING:
    import argparse

    from ..container import Container, System

log = logging.getLogger(__name__)


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


@dataclass
class BuildInfo:
    """
    Information gathered during a build
    """
    # True if the build was successful
    success: bool = False
    # List of container paths for artifacts
    artifacts: List[str] = field(default_factory=list)


class Builder:
    """
    Interface for classes providing the logic for CI builds
    """
    # Registry of known builders
    builders: Dict[str, Type[Builder]] = {}

    # Callbacks to use to add extra command line args
    extra_args_callbacks: List[Callable] = []

    # BuildInfo (sub)class used by this builder
    build_info_cls: Type[BuildInfo] = BuildInfo

    add_arguments: Callable

    @classmethod
    def register(cls, builder_cls: Type["Builder"]) -> Type["Builder"]:
        name = getattr(builder_cls, "NAME", None)
        if name is None:
            name = builder_cls.__name__.lower()
        cls.builders[name] = builder_cls

        # Register extra_args callbacks.
        #
        # Only register callbacks that are in the class __dict__ to avoid
        # inheritance, which would register command line options from base
        # classes multiple times
        if "add_arguments" in builder_cls.__dict__:
            cls.extra_args_callbacks.append(builder_cls.add_arguments)

        return builder_cls

    @classmethod
    def list(cls) -> List[str]:
        return list(cls.builders.keys())

    @classmethod
    def create_builder(cls, name: str, **kw) -> "Builder":
        builder_cls = cls.builders[name.lower()]
        return builder_cls.create(**kw)

    @classmethod
    def create(cls, **kw: Any) -> "Builder":
        raise NotImplementedError(f"The builder {cls} cannot be instantiated")

    @classmethod
    def detect(cls, *, system: System, **kw) -> "Builder":
        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro
        if isinstance(system.distro, DebianDistro):
            return cls.builders["debian"].create(system=system, **kw)
        elif isinstance(system.distro, RpmDistro):
            return cls.builders["rpm"].create(system=system, **kw)
        else:
            raise NotImplementedError(f"No suitable builder found for distribution {system.distro!r}")

    def __init__(self, *, system: System, srcdir: str, args: argparse.Namespace):
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

    @host_only
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

    @guest_only
    def setup_container_guest(self):
        """
        Set up the build environment in the container
        """
        pass

    @host_only
    def log_capture_start(self, log_file: str):
        self.buildlog_file = open(log_file, "wt")
        self.buildlog_handler = logging.StreamHandler(self.buildlog_file)
        self.buildlog_handler.setLevel(logging.DEBUG)
        self.buildlog_handler.setFormatter(
                logging.Formatter("%(asctime)-19.19s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(self.buildlog_handler)
        logging.getLogger().setLevel(logging.DEBUG)

    @host_only
    def log_capture_end(self):
        if self.buildlog_handler is not None:
            logging.getLogger().removeHandler(self.buildlog_handler)
            self.buildlog_handler = None
            self.buildlog_file.close()
            self.buildlog_file = None

    @host_only
    def get_build_deps(self) -> List[str]:
        """
        Return a list of packages to be installed as build-depedencies to build
        this source
        """
        raise NotImplementedError(f"{self.__class__.__name__}.get_build_deps is not implemented")

    @host_only
    @contextlib.contextmanager
    def container(self):
        """
        Start a container to run CI operations
        """
        container_config = ContainerConfig()
        # Mount the source directory as /srv/moncic-ci/source/<name>
        # Set it as the default current directory in the container
        # Mounted volatile to prevent changes to it
        container_config.configure_workdir(self.srcdir, bind_type="volatile", mountpoint="/srv/moncic-ci/source")
        container = self.system.create_container(config=container_config)
        with container:
            self.setup_container_host(container)
            try:
                yield container
            finally:
                self.log_capture_end()

    @host_only
    def build(self, shell: bool = False, source_only: bool = False) -> BuildInfo:
        """
        Run the build, store the artifacts in the given directory if requested,
        return the returncode of the build process
        """
        artifacts_dir = self.system.images.session.moncic.config.build_artifacts_dir
        with self.container() as container:
            # General builder information
            log.info("Builder: %s", self.__class__.__name__)
            # Log moncic config
            moncic_config = self.system.images.session.moncic.config
            for fld in dataclasses.fields(moncic_config):
                log.debug("moncic:%s = %r", fld.name, getattr(moncic_config, fld.name))
            # Log container config
            for fld in dataclasses.fields(container.config):
                log.debug("container:%s = %r", fld.name, getattr(container.config, fld.name))

            # Build run config
            run_config = container.config.run_config()
            run_config.user = UserConfig.root()
            # Log run config
            for fld in dataclasses.fields(run_config):
                log.debug("run:%s = %r", fld.name, getattr(run_config, fld.name))

            try:
                build_info = container.run_callable(
                        self.build_in_container,
                        run_config,
                        kwargs={"source_only": source_only}).result()
                if artifacts_dir:
                    self.collect_artifacts(container, build_info, artifacts_dir)
            finally:
                if shell:
                    run_config = container.config.run_config()
                    run_config.interactive = True
                    run_config.check = False
                    run_config.user = UserConfig.root()
                    run_config.cwd = "/srv/moncic-ci/build"
                    container.run_shell(config=run_config)
        return build_info

    @guest_only
    def build_in_container(self, source_only: bool = False) -> BuildInfo:
        """
        Run the build in a child process.

        The function will be called inside the running system.

        The current directory will be set to the source directory in /srv/moncic-ci/source/<name>.

        Standard output and standard error are logged.
        """
        build_info = self.build_info_cls()
        self.setup_container_guest()
        return build_info

    @host_only
    def collect_artifacts(self, container: Container, destdir: str):
        """
        Copy build artifacts to the given directory
        """
        # Do nothing by default
        pass

    @classmethod
    def analyze(cls, analyzer: Analyzer):
        """
        Run consistency checks on the given source directory, using all
        available build styles
        """
        cls.builders["debian"].analyze(analyzer)
        cls.builders["rpm"].analyze(analyzer)
        # TODO: check that NEWS.md version matches upstream version
