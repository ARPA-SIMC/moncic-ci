from __future__ import annotations

import dataclasses
import logging
import os
from functools import cached_property
from typing import TYPE_CHECKING, Any

from moncic.container import ContainerConfig, RunConfig, UserConfig
from moncic.distro import DistroFamily
from moncic.system import System
from .image import NspawnImage

if TYPE_CHECKING:
    import subprocess

    from moncic.container import Container
    from moncic.distro import Distro
    from moncic.imagestorage import Images

log = logging.getLogger(__name__)


class NspawnSystem(System):
    """
    A system configured in the CI.

    System objects hold the system configuration and contain factory methods to
    instantiate objects used to work with, and maintain, the system
    """

    image: NspawnImage

    def __init__(self, images: Images, config: NspawnImage, path: str | None = None) -> None:
        super().__init__(images, config)
        self.images = images
        self.config = config
        self.log = config.logger
        if path is None:
            self.path = self.config.path
        else:
            self.path = path

    def __repr__(self) -> str:
        return f"{self.distro}@{self.path}"

    def _get_distro(self) -> Distro:
        """
        Return the distribution this system is based on
        """
        if self.config.extends is not None:
            with self.images.system(self.config.extends) as parent:
                return parent.distro
        elif self.config.distro is not None:
            return DistroFamily.lookup_distro(self.config.distro)
        else:
            raise RuntimeError("System configuration has neither `extends` nor `distro` set")

    def is_bootstrapped(self) -> bool:
        """
        Check if the image has been bootstrapped
        """
        return os.path.exists(self.path)

    def local_run(self, cmd: list[str], config: RunConfig | None = None) -> subprocess.CompletedProcess:
        """
        Run a command on the host system.

        This is used for bootstrapping or removing a system.
        """
        # Import here to avoid dependency loops
        from moncic.runner import LocalRunner

        return LocalRunner.run(self.log, cmd, config, self.config)

    def _container_chain_forwards_users(self) -> list[str]:
        """
        Check if any container in the chain forwards users
        """
        res = set(self.config.forward_users)
        if self.config.extends is not None:
            with self.images.system(self.config.extends) as parent:
                res.update(parent._container_chain_forwards_users())
        return sorted(res)

    def _container_chain_package_list(self) -> list[str]:
        """
        Concatenate the requested package lists for all containers in the
        chain
        """
        res = []
        if self.config.extends is not None:
            with self.images.system(self.config.extends) as parent:
                res.extend(parent._container_chain_package_list())
        res.extend(self.distro.get_base_packages())
        res.extend(self.config.packages)
        return res

    def _container_chain_config_package_list(self) -> list[str]:
        """
        Concatenate the requested package lists for all containers in the
        chain
        """
        res = []
        if self.config.extends is not None:
            with self.images.system(self.config.extends) as parent:
                res.extend(parent._container_chain_config_package_list())
        res.extend(self.config.packages)
        return res

    def _container_chain_maintscripts(self) -> list[str]:
        """
        Build a script with the concatenation of all scripts coming from
        calling distro.get_{name}_script on all the containers in the chain
        """
        res = []
        if self.config.extends is not None:
            with self.images.system(self.config.extends) as parent:
                res.extend(parent._container_chain_maintscripts())
        if self.config.maintscript:
            res.append(self.config.maintscript)
        return res

    def _update_container(self, container: Container):
        """
        Run update machinery on a container.
        """
        # Forward users if needed
        for u in self._container_chain_forwards_users():
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
        for pkg in self._container_chain_package_list():
            if pkg in seen:
                continue
            packages.append(pkg)
            seen.add(pkg)

        # Install packages
        for cmd in self.distro.get_install_packages_script(self, packages):
            container.run(cmd)

        # Run maintscripts
        for script in self._container_chain_maintscripts():
            container.run_script(script)

    def describe_container(self) -> dict[str, Any]:
        """
        Return a dictionary describing facts about the container
        """
        res: dict[str, Any] = {}

        # Forward users if needed
        if users_forwarded := self._container_chain_forwards_users():
            res["users_forwarded"] = users_forwarded

        # Build list of packages to install, removing duplicates
        packages: set[str] = set()
        for pkg in self._container_chain_config_package_list():
            packages.add(pkg)

        res["packages_required"] = sorted(packages)

        if packages:
            with self.create_container() as container:
                try:
                    res["packages_installed"] = dict(
                        container.run_callable(self.distro.get_versions, args=(res["packages_required"],)).result()
                    )
                except NotImplementedError as e:
                    self.log.info("cannot get details of how package requirements have been resolved: %s", e)
        else:
            res["packages_installed"] = {}

        # Describe maintscripts
        if scripts := self._container_chain_maintscripts():
            res["maintscripts"] = scripts

        return res

    def container_config(self, config: ContainerConfig | None = None) -> ContainerConfig:
        """
        Create or complete a ContainerConfig
        """
        if config is None:
            config = ContainerConfig()
            if self.config.tmpfs is not None:
                config.tmpfs = self.config.tmpfs
            else:
                config.tmpfs = self.images.session.moncic.config.tmpfs
        elif config.ephemeral and config.tmpfs is None:
            # Make a copy to prevent changing the caller's config
            config = dataclasses.replace(config)
            if self.config.tmpfs is not None:
                config.tmpfs = self.config.tmpfs
            else:
                config.tmpfs = self.images.session.moncic.config.tmpfs

        # Allow distro-specific setup
        self.distro.container_config_hook(self, config)

        # Force ephemeral to True in plain systems
        config.ephemeral = True

        return config

    def create_container(self, instance_name: str | None = None, config: ContainerConfig | None = None) -> Container:
        """
        Boot a container with this system
        """
        config = self.container_config(config)

        # Import here to avoid an import loop
        from moncic.container import NspawnContainer

        return NspawnContainer(self, config, instance_name)


class MaintenanceMixin(NspawnSystem):
    def container_config(self, config: ContainerConfig | None = None) -> ContainerConfig:
        config = super().container_config(config)
        # Force ephemeral to False in maintenance systems
        config.ephemeral = False
        return config

    def update(self):
        """
        Run periodic maintenance on the system
        """
        with self.create_container(config=ContainerConfig(ephemeral=False)) as container:
            self._update_container(container)
        self._update_cachedir()

    def _update_cachedir(self):
        """
        Create or remove a CACHEDIR.TAG file, depending on the image
        configuration
        """
        cachedir_pathname = os.path.join(self.path, "CACHEDIR.TAG")
        if self.config.backup:
            try:
                os.unlink(cachedir_pathname)
            except FileNotFoundError:
                pass
        else:
            if not os.path.exists(cachedir_pathname):
                with open(cachedir_pathname, "wt") as fd:
                    # See https://bford.info/cachedir/
                    print("Signature: 8a477f597d28d172789f06886806bc55", file=fd)
                    print("# This file hints to backup software that they can skip this directory.", file=fd)
                    print("# See https://bford.info/cachedir/", file=fd)


class MaintenanceSystem(MaintenanceMixin, NspawnSystem):
    """
    System used to do maintenance on an OS image
    """
