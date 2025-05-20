import abc
import contextlib
import logging
import shutil
import subprocess
from functools import cached_property
from pathlib import Path
from typing import (TYPE_CHECKING, Any, ContextManager, NamedTuple,
                    Optional, Self, override)
from collections.abc import Generator

import yaml

from moncic.container import RunConfig
from moncic.distro import Distro, DistroFamily
from moncic.image import Image, ImageType
from moncic.utils.btrfs import Subvolume

if TYPE_CHECKING:
    from moncic.container import Container, ContainerConfig
    from moncic.moncic import MoncicConfig

    from .images import NspawnImages

log = logging.getLogger("nspawn")


class BootstrapInfo(NamedTuple):
    """Information used to bootstrap an image."""

    # Name of the distribution used to bootstrap this image.
    # If missing, this image needs to be created from an existing image
    distro: str | None
    # Name of the distribution used as a base for this one.
    # If missing, this image needs to be created by bootstrapping from scratch
    extends: str | None
    # List of packages to install
    packages: list[str]
    # Contents of a script to run for system maintenance
    maintscript: str | None
    # List of users to propagate from host to image during maintenance
    forward_users: list[str]
    # When False, a CACHEDIR.TAG is created in the container image as a hint
    # for backup programs to skip backing up an image that can be recreated
    # from scratch
    backup: bool
    # Btrfs compression level to set on the OS image subvolume when it is
    # created. The value is the same as can be set by `btrfs property set
    # compression`. Default: the global 'compression' setting. You can use 'no'
    # or 'none' to ask for no compression when one globally is set.
    compression: str | None

    @classmethod
    def from_distro(cls, distro: Distro) -> Self:
        return cls(
            distro=distro.name,
            extends=None,
            packages=[],
            maintscript=None,
            forward_users=[],
            backup=False,
            compression=None,
        )

    @classmethod
    def load(cls, conf: dict[str, Any]) -> Self:
        """
        Create from a parsed config file.

        Remove parsed elements from dict.
        """
        distro = conf.pop("distro", None)
        extends = conf.pop("extends", None)

        # Make sure forward_users, if present, is a list of strings

        if (fu := conf.pop("forward_user", None)) is None:
            forward_users = []
        elif isinstance(fu, str):
            forward_users = [fu]
        else:
            forward_users = [str(e) for e in fu]

        # Prepend a default shebang to the maintscript if missing
        maintscript = conf.pop("maintscript", None)
        if maintscript is not None and not maintscript.startswith("#!"):
            maintscript = "#!/bin/sh\n" + maintscript

        return cls(
            distro=distro,
            extends=extends,
            packages=conf.pop("packages", None) or [],
            maintscript=maintscript or None,
            forward_users=forward_users,
            backup=conf.pop("backup", False),
            compression=conf.pop("compression", None),
        )


class ContainerInfo(NamedTuple):
    """Information used to start a container."""

    # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
    #
    # Leave to None to use system or container defaults.
    tmpfs: bool | None = None

    @classmethod
    def load(cls, conf: dict[str, Any]) -> Self:
        """
        Create from a parsed config file.

        Remove parsed elements from dict.
        """
        return cls(
            tmpfs=conf.pop("tmpfs", None),
        )


class NspawnImage(Image, abc.ABC):
    """
    Configuration for a system
    """

    images: "NspawnImages"

    def __init__(
        self,
        *,
        images: "NspawnImages",
        name: str,
        distro: Distro,
        path: Path,
    ) -> None:
        try:
            bootstrapped = path.exists()
        except PermissionError:
            with images.session.moncic.privs.root():
                bootstrapped = path.exists()

        super().__init__(
            images=images, image_type=ImageType.NSPAWN, name=name, distro=distro, bootstrapped=bootstrapped
        )
        #: Path to the image on disk
        self.path: Path = path
        #: Path to the config file
        self.config_path: Path | None = None
        #: Information used to bootstrap the image
        self.bootstrap_info: BootstrapInfo = BootstrapInfo.from_distro(distro)
        #: Information used to start containers
        self.container_info: ContainerInfo = ContainerInfo()

    @classmethod
    def find_config(cls, mconfig: "MoncicConfig", imagedir: Path, name: str) -> Path | None:
        """
        Find the configuration file for the given image
        """
        for path in [imagedir] + mconfig.imageconfdirs:
            conf_path = path / f"{name}.yaml"
            log.debug("%s: look for configuration on %s", name, conf_path)
            try:
                if conf_path.exists():
                    log.debug("%s: configuration found at %s", name, conf_path)
                    return conf_path
            except PermissionError:
                pass
        return None

    @classmethod
    def load(cls, mconfig: "MoncicConfig", images: "NspawnImages", name: str) -> Self:
        """
        Load the configuration from the given path setup.

        If a .yaml file exists, it is used.

        Otherwise, if an os tree exists, configuration is inferred from it.

        Otherwise, configuration is inferred from the basename of the path,
        which is assumed to be a distribution name.
        """
        if conf_path := cls.find_config(mconfig, images.imagedir, name):
            with conf_path.open() as fd:
                conf = yaml.load(fd, Loader=yaml.CLoader)
        else:
            conf = None

        image_path = (images.imagedir / name).absolute()
        log.debug("%s: image pathname: %s", name, image_path)

        # Find distro
        if conf is None:
            try:
                if image_path.exists():
                    distro = DistroFamily.from_path(image_path)
                else:
                    distro = DistroFamily.lookup_distro(name)
            except PermissionError:
                privs = images.session.moncic.privs
                with privs.root():
                    if image_path.exists():
                        distro = DistroFamily.from_path(image_path)
                    else:
                        distro = DistroFamily.lookup_distro(name)
            return cls(images=images, name=name, distro=distro, path=image_path)

        distro_name = conf.pop("distro", None)
        extends_name = conf.pop("extends", None)
        if distro_name and extends_name:
            raise RuntimeError(f"{name}: both 'distro' and 'extends' have been specified")
        elif not distro_name and not extends_name:
            raise RuntimeError(f"{name}: neither 'distro' nor 'extends' have been specified")

        if distro_name:
            distro = DistroFamily.lookup_distro(distro_name)
        else:
            parent = images.image(extends_name)
            distro = parent.distro

        image = cls(images=images, name=name, path=image_path, distro=distro)
        image.config_path = conf_path
        image.bootstrap_info = BootstrapInfo.load(conf)
        image.container_info = ContainerInfo.load(conf)

        for key in conf.keys():
            log.debug("%s: ignoring unsupported configuration: %r", conf_path, key)

        return image

    def local_run(self, cmd: list[str], config: RunConfig | None = None) -> subprocess.CompletedProcess:
        """
        Run a command on the host system.

        This is used for bootstrapping or removing a system.
        """
        from moncic.runner import LocalRunner

        return LocalRunner.run(self.logger, cmd, config, image=self)

    @override
    def container(self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None) -> "Container":
        from .container import NspawnContainer

        return NspawnContainer(self, path=self.path, config=config, instance_name=instance_name)

    @override
    def maintenance_container(
        self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None
    ) -> "Container":
        from .container import NspawnMaintenanceContainer

        with self.transactional_workdir() as work_path:
            return NspawnMaintenanceContainer(self, path=work_path, config=config, instance_name=instance_name)

    @override
    def get_backend_id(self) -> str:
        return self.path.as_posix()

    @override
    def remove_config(self) -> None:
        if self.config_path is None:
            return
        log.info("%s: removing image configuration file", self.config_path)
        self.config_path.unlink()

    @cached_property
    def forwards_users(self) -> list[str]:
        """
        Check if any container in the chain forwards users
        """
        res = set(self.bootstrap_info.forward_users)
        if self.bootstrap_info.extends is not None:
            parent = self.images.image(self.bootstrap_info.extends)
            res.update(parent.forwards_users)
        return sorted(res)

    @cached_property
    def package_list(self) -> list[str]:
        """
        Concatenate the requested package lists for all containers in the
        chain
        """
        res = []
        if self.bootstrap_info.extends is not None:
            parent = self.images.image(self.bootstrap_info.extends)
            res.extend(parent.package_list)
        res.extend(self.distro.get_base_packages())
        res.extend(self.bootstrap_info.packages)
        return res

    @cached_property
    def config_package_list(self) -> list[str]:
        """
        Concatenate the requested package lists for all containers in the
        chain
        """
        res = []
        if self.bootstrap_info.extends is not None:
            parent = self.images.image(self.bootstrap_info.extends)
            res.extend(parent.config_package_list)
        res.extend(self.bootstrap_info.packages)
        return res

    @cached_property
    def maintscripts(self) -> list[str]:
        """
        Build a script with the concatenation of all scripts coming from
        calling distro.get_{name}_script on all the containers in the chain
        """
        res = []
        if self.bootstrap_info.extends is not None:
            parent = self.images.image(self.bootstrap_info.extends)
            res.extend(parent.maintscripts)
        if self.bootstrap_info.maintscript:
            res.append(self.bootstrap_info.maintscript)
        return res

    @override
    def describe_container(self) -> dict[str, Any]:
        """
        Return a dictionary describing facts about the container
        """
        res: dict[str, Any] = super().describe_container()

        # Forward users if needed
        if users_forwarded := self.forwards_users:
            res["users_forwarded"] = users_forwarded

        # Build list of packages to install, removing duplicates
        packages: set[str] = set()
        for pkg in self.config_package_list:
            packages.add(pkg)

        res["packages_required"] = sorted(packages)

        # TODO: move to parent image once we can instantiate containers?
        # if packages:
        #     with self.create_container() as container:
        #         try:
        #             res["packages_installed"] = dict(
        #                 container.run_callable(self.distro.get_versions, args=(res["packages_required"],)).result()
        #             )
        #         except NotImplementedError as e:
        #             self.log.info("cannot get details of how package requirements have been resolved: %s", e)
        # else:
        #     res["packages_installed"] = {}

        # Describe maintscripts
        if scripts := self.maintscripts:
            res["maintscripts"] = scripts

        return res

    def _update_container(self, container: "Container"):
        """
        Run update machinery on a container.
        """
        from moncic.runner import UserConfig

        # Forward users if needed
        for u in self.forwards_users:
            container.forward_user(UserConfig.from_user(u), allow_maint=True)

        # Setup network
        for cmd in self.distro.get_setup_network_script(self):
            container.run(cmd)

        # Update package databases
        for cmd in self.distro.get_update_pkgdb_script(self):
            container.run(cmd)

        # Upgrade system packages
        for cmd in self.distro.get_upgrade_system_script(self):
            container.run(cmd)

        # Build list of packages to install, removing duplicates
        packages: list[str] = []
        seen: set[str] = set()
        for pkg in self.package_list:
            if pkg in seen:
                continue
            packages.append(pkg)
            seen.add(pkg)

        # Install packages
        for cmd in self.distro.get_install_packages_script(self, packages):
            container.run(cmd)

        # Run maintscripts
        for script in self.maintscripts:
            container.run_script(script)

    @abc.abstractmethod
    def _extend_parent(self, path: Path) -> None:
        """Initialize self.path with a clone of the parent image."""

    @abc.abstractmethod
    def _bootstrap_new(self, path: Path) -> None:
        """Bootstrap a new OS image from scratch."""

    @abc.abstractmethod
    def transactional_workdir(self) -> ContextManager[Path]:
        """
        Create a working directory for transactional maintenance of the image.

        If the code is successful, the image in the working directory will
        replace the original one.
        """

    @override
    def bootstrap(self) -> None:
        if self.path.exists():
            return

        if self.bootstrap_info is None:
            raise RuntimeError(f"{self.name} has no bootstrap information")

        log.info("%s: bootstrapping directory", self.name)

        with self.transactional_workdir() as work_path:
            if self.bootstrap_info.extends is not None:
                self._extend_parent(work_path)
            else:
                self._bootstrap_new(work_path)

    def update(self):
        """
        Run periodic maintenance on the system
        """
        with self.maintenance_container() as container:
            self._update_container(container)
        self._update_cachedir()

    def _update_cachedir(self):
        """
        Create or remove a CACHEDIR.TAG file, depending on the image
        configuration
        """
        cachedir_path = self.path / "CACHEDIR.TAG"
        if self.backup:
            try:
                cachedir_path.unlink()
            except FileNotFoundError:
                pass
        else:
            if not cachedir_path.exists():
                with cachedir_path.open("wt") as fd:
                    # See https://bford.info/cachedir/
                    print("Signature: 8a477f597d28d172789f06886806bc55", file=fd)
                    print("# This file hints to backup software that they can skip this directory.", file=fd)
                    print("# See https://bford.info/cachedir/", file=fd)


class NspawnImagePlain(NspawnImage):
    @override
    @contextlib.contextmanager
    def transactional_workdir(self) -> Generator[Path, None, None]:
        if self.path.exists():
            self.logger.info("%s: transactional updates on non-btrfs nspawn images are not supported", self.path)
            yield self.path
        else:
            work_path = self.path.parent / f"{self.path.name}.new"
            try:
                yield work_path
            except BaseException:
                if work_path.exists():
                    shutil.rmtree(work_path)
                raise
            else:
                if work_path.exists():
                    work_path.rename(self.path)

    @override
    def _extend_parent(self, path: Path) -> None:
        assert self.bootstrap_info.extends is not None
        parent = self.images.image(self.bootstrap_info.extends)
        assert isinstance(parent, NspawnImage)
        self.local_run(["cp", "--reflink=auto", "-a", parent.path.as_posix(), path.as_posix()])

    @override
    def _bootstrap_new(self, path: Path) -> None:
        from .container import NspawnMaintenanceContainer

        tarball_path = self.images.get_distro_tarball(self.distro)
        if tarball_path is not None:
            # Shortcut in case we have a chroot in a tarball
            self.path.mkdir()
            self.local_run(["tar", "-C", self.path.as_posix(), "-axf", path.as_posix()])
        else:
            container = NspawnMaintenanceContainer(self, path=path)
            self.distro.bootstrap(container)

    @override
    def remove(self) -> None:
        if not self.path.exists():
            return
        shutil.rmtree(self.path)


class NspawnImageBtrfs(NspawnImage):
    @override
    @contextlib.contextmanager
    def transactional_workdir(self) -> Generator[Path, None, None]:
        work_path = self.path.parent / f"{self.path.name}.new"
        subvolume = Subvolume(self.images.session.moncic.config, work_path, self.bootstrap_info.compression)
        if not self.path.exists():
            with subvolume.create():
                try:
                    yield work_path
                except BaseException:
                    subvolume.remove()
                    raise
                else:
                    work_path.rename(self.path)
        else:
            subvolume = Subvolume(self.images.session.moncic.config, work_path, self.bootstrap_info.compression)
            # Create work_path as a snapshot of path
            subvolume.snapshot(self.path)
            try:
                yield work_path
            except BaseException:
                subvolume.remove()
                raise
            else:
                subvolume.replace_subvolume(self.path)

    @override
    def _extend_parent(self, path: Path) -> None:
        assert self.bootstrap_info.extends is not None
        parent = self.images.image(self.bootstrap_info.extends)
        assert isinstance(parent, NspawnImage)
        subvolume = Subvolume(self.images.session.moncic.config, path, self.bootstrap_info.compression)
        subvolume.snapshot(parent.path)

    @override
    def _bootstrap_new(self, path: Path) -> None:
        from .container import NspawnMaintenanceContainer

        tarball_path = self.images.get_distro_tarball(self.distro)
        subvolume = Subvolume(self.images.session.moncic.config, self.path, self.bootstrap_info.compression)
        with subvolume.create():
            if tarball_path is not None:
                # Shortcut in case we have a chroot in a tarball
                self.local_run(["tar", "-C", path.as_posix(), "-axf", tarball_path.as_posix()])
            else:
                container = NspawnMaintenanceContainer(self, path=path)
                self.distro.bootstrap(container)

    @override
    def remove(self) -> None:
        if not self.path.exists():
            return
        subvolume = Subvolume(self.images.session.moncic.config, self.path, self.bootstrap_info.compression)
        subvolume.remove()
