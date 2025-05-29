import abc
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, override

from moncic import context
from moncic.distro import Distro
from moncic.image import BootstrappableImage, ImageType, RunnableImage
from moncic.utils.btrfs import Subvolume

if TYPE_CHECKING:
    from moncic.container import Container, ContainerConfig, MaintenanceContainer
    from moncic.provision.config import ContainerInfo

    from .images import NspawnImages

log = logging.getLogger("nspawn")


class NspawnImage(RunnableImage, abc.ABC):
    """Boostrapped nspawn image."""

    images: "NspawnImages"

    def __init__(
        self,
        *,
        images: "NspawnImages",
        name: str,
        distro: Distro,
        path: Path,
    ) -> None:
        super().__init__(images=images, image_type=ImageType.NSPAWN, name=name, distro=distro)
        #: Image storage for this image
        self.images = images
        #: Path to the image on disk
        self.path: Path = path

    @override
    def container(self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None) -> "Container":
        from moncic.container import ContainerConfig

        from .container import NspawnContainer

        return NspawnContainer(self, config=config or ContainerConfig(), instance_name=instance_name)

    @override
    def maintenance_container(
        self, *, instance_name: str | None = None, config: Optional["ContainerConfig"] = None
    ) -> "MaintenanceContainer":
        from moncic.container import ContainerConfig

        from .container import NspawnMaintenanceContainer

        return NspawnMaintenanceContainer(self, config=config or ContainerConfig(), instance_name=instance_name)

    @override
    def get_backend_id(self) -> str:
        return self.path.as_posix()

    @override
    def describe(self) -> dict[str, Any]:
        """
        Return a dictionary describing facts about the container
        """
        res: dict[str, Any] = super().describe()
        if self.bootstrap_from is None:
            return res

        # Forward users if needed
        if users_forwarded := self.bootstrap_from.forwards_users:
            res["users_forwarded"] = users_forwarded

        # Build list of packages to install, removing duplicates
        packages: set[str] = set()
        for pkg in self.bootstrap_from.config_package_list:
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
        if scripts := self.bootstrap_from.maintscripts:
            res["maintscripts"] = scripts

        return res

    @override
    def update(self) -> None:
        """
        Run periodic maintenance on the system
        """
        super().update()
        self._update_cachedir()

    def _update_cachedir(self) -> None:
        """
        Create or remove a CACHEDIR.TAG file, depending on the image
        configuration
        """
        from moncic.provision.image import ConfiguredImage

        match self.bootstrap_from:
            case ConfiguredImage():
                backup = self.bootstrap_from.config.bootstrap_info.backup
            case _:
                return

        with context.privs.root():
            cachedir_path = self.path / "CACHEDIR.TAG"
            if backup:
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
    def get_container_info(self) -> "ContainerInfo":
        res = super().get_container_info()
        # Force using tmpfs backing for ephemeral containers, since we cannot
        # use snapshots
        if not res.tmpfs:
            res = res._replace(tmpfs=True)
        return res

    @override
    def remove(self) -> BootstrappableImage | None:
        with context.privs.root():
            if self.path.exists():
                shutil.rmtree(self.path)
        return self.bootstrap_from


class NspawnImageBtrfs(NspawnImage):
    @override
    def remove(self) -> BootstrappableImage | None:
        with context.privs.root():
            if self.path.exists():
                subvolume = Subvolume(self.session.images.session.moncic.config, self.path, None)
                subvolume.remove()
        return self.bootstrap_from
