import abc
from functools import cached_property
import contextlib
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Self, override, ContextManager, Generator, NamedTuple, Any

import yaml

from moncic.container import RunConfig
from moncic.image import Image, ImageType
from moncic.distro import DistroFamily, Distro
from moncic.utils.btrfs import Subvolume

if TYPE_CHECKING:
    from moncic.moncic import MoncicConfig
    from .images import NspawnImages
    from .system import NspawnSystem, MaintenanceSystem

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

        return LocalRunner.run(self.logger, cmd, config, system_config=self)

    @override
    def get_backend_id(self) -> str:
        return self.path.as_posix()

    @override
    def remove_config(self) -> None:
        if self.config_path is None:
            return
        log.info("%s: removing image configuration file", self.config_path)
        self.config_path.unlink()

    @abc.abstractmethod
    def maintenance_system(self) -> ContextManager["MaintenanceSystem"]:
        """
        Instantiate a MaintenanceSystem that can only be used for the duration
        of this context manager.

        This allows maintenance to be transactional, limited to backends that
        support it, so that errors in the maintenance roll back to the previous
        state and do not leave an inconsistent OS image
        """

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


class NspawnImagePlain(NspawnImage):
    @override
    def bootstrap(self) -> None:
        from .system import MaintenanceSystem

        if self.path.exists():
            return

        if self.bootstrap_info is None:
            raise RuntimeError(f"{self.name} has no bootstrap information")

        log.info("%s: bootstrapping directory", self.name)

        orig_path = self.path
        work_path = self.path.parent / f"{self.path.name}.new"
        try:
            self.path = work_path
            try:
                if self.bootstrap_info.extends is not None:
                    parent = self.images.image(self.extends)
                    assert isinstance(parent, NspawnImage)
                    self.local_run(["cp", "--reflink=auto", "-a", parent.path.as_posix(), work_path.as_posix()])
                else:
                    tarball_path = self.images.get_distro_tarball(self.bootstrap_info.distro)
                    if tarball_path is not None:
                        # Shortcut in case we have a chroot in a tarball
                        os.mkdir(work_path)
                        self.local_run(["tar", "-C", work_path.as_posix(), "-axf", tarball_path])
                    else:
                        system = MaintenanceSystem(self.images, self)
                        self.distro.bootstrap(system)
            except BaseException:
                shutil.rmtree(work_path)
                raise
            else:
                if work_path.exists():
                    work_path.rename(orig_path)
        finally:
            self.path = orig_path

    @override
    def remove(self) -> None:
        if not self.path.exists():
            return
        shutil.rmtree(self.path)

    @contextlib.contextmanager
    def system(self) -> Generator["NspawnSystem", None, None]:
        from .system import NspawnSystem

        yield NspawnSystem(self.images, self)

    @contextlib.contextmanager
    def maintenance_system(self) -> Generator["MaintenanceSystem", None, None]:
        from .system import MaintenanceSystem

        yield MaintenanceSystem(self.images, self)


class NspawnImageBtrfs(NspawnImage):
    @override
    def bootstrap(self) -> None:
        from .system import MaintenanceSystem

        if self.path.exists():
            return

        log.info("%s: bootstrapping subvolume", self.name)

        orig_path = self.path
        work_path = self.path.parent / f"{self.path.name}.new"
        try:
            self.path = work_path
            try:
                if self.extends is not None:
                    parent = self.images.image(self.extends)
                    assert isinstance(parent, NspawnImage)
                    subvolume = Subvolume(self, self.images.session.moncic.config)
                    subvolume.snapshot(parent.path)
                else:
                    tarball_path = self.images.get_distro_tarball(self.distro)
                    subvolume = Subvolume(self, self.images.session.moncic.config)
                    with subvolume.create():
                        if tarball_path is not None:
                            # Shortcut in case we have a chroot in a tarball
                            self.local_run(["tar", "-C", work_path.as_posix(), "-axf", tarball_path])
                        else:
                            system = MaintenanceSystem(self.images, self)
                            distro = DistroFamily.lookup_distro(self.distro)
                            distro.bootstrap(system)
            except BaseException:
                shutil.rmtree(work_path)
                raise
            else:
                if work_path.exists():
                    work_path.rename(orig_path)
        finally:
            self.path = orig_path

    @override
    def remove(self) -> None:
        if not self.path.exists():
            return
        subvolume = Subvolume(self, self.images.session.moncic.config)
        subvolume.remove()

    @contextlib.contextmanager
    def system(self) -> Generator["NspawnSystem", None, None]:
        from .system import NspawnSystem

        yield NspawnSystem(self.images, self)

    @contextlib.contextmanager
    def maintenance_system(self) -> Generator["MaintenanceSystem", None, None]:
        from .system import MaintenanceSystem

        orig_path = self.path
        work_path = self.path.parent / f"{self.path.name}.new"
        if work_path.exists():
            raise RuntimeError(f"Found existing {work_path} which should be removed")

        self.path = work_path
        try:
            if not orig_path.exists():
                # Transactional work on a new path
                try:
                    yield MaintenanceSystem(self.images, self)
                except BaseException:
                    # TODO: remove work_path is currently not needed as System is
                    #       doing it. Maybe move that here?
                    raise
                else:
                    if work_path.exists():
                        work_path.rename(orig_path)
            else:
                # Update
                subvolume = Subvolume(self, self.images.session.moncic.config)
                # Create work_path as a snapshot of path
                subvolume.snapshot(orig_path)
                try:
                    yield MaintenanceSystem(self.images, self)
                except BaseException:
                    self.logger.warning("Rolling back maintenance changes")
                    subvolume.remove()
                    raise
                else:
                    self.logger.info("Committing maintenance changes")
                    # Swap and remove
                    subvolume.replace_subvolume(orig_path)
        finally:
            self.path = orig_path
