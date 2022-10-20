from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from typing import TYPE_CHECKING, Dict, List, Optional, Type

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


def link_or_copy(src: str, dstdir: str, user: Optional[UserConfig] = None):
    """
    Try to make a hardlink of src inside directory dstdir.

    If hardlinking is not possible, copy it
    """
    dest = os.path.join(dstdir, os.path.basename(src))
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
    def create(cls, name: str, system: System, srcdir: str) -> "Builder":
        builder_cls = cls.builders[name]
        return builder_cls(system, srcdir)

    @classmethod
    def detect(cls, system: System, srcdir: str) -> "Builder":
        if isinstance(system.distro, distro.DebianDistro):
            cls.builders["debian"].create(system, srcdir)
        elif isinstance(system.distro, distro.RpmDistro):
            cls.builders["rpm"].create(system, srcdir)
        else:
            raise NotImplementedError(f"No suitable builder found for distribution {system.distro!r}")

    def __init__(self, system: System, srcdir: str):
        """
        The constructor is run in the host system
        """
        self.system = system
        self.srcdir = srcdir
        self.user = UserConfig.from_sudoer()

    def build(self, shell: bool = False) -> int:
        """
        Run the build, store the artifacts in the given directory if requested,
        return the returncode of the build process
        """
        artifacts_dir = self.system.images.session.moncic.config.build_artifacts_dir
        container_config = ContainerConfig()
        container_config.configure_workdir(self.srcdir, bind_type="volatile", mountpoint="/srv/moncic-ci/source")
        container = self.system.create_container(config=container_config)
        with container:
            container_root = container.get_root()
            os.makedirs(os.path.join(container_root, "srv", "moncic-ci", "build"), exist_ok=True)
            build_config = container_config.run_config()
            build_config.user = UserConfig.root()
            try:
                res = container.run_callable(
                        self.build_in_container,
                        build_config,
                        kwargs={"workdir": "/srv/moncic-ci/build"})
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
        return res.returncode

    def build_in_container(self, workdir: str) -> Optional[int]:
        """
        Run the build in a child process.

        The function will be callsed inside the running system.

        The current directory will be set to the source directory.

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
