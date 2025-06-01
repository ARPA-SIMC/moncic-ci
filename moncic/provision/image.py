from typing import TYPE_CHECKING, override, Optional

from moncic.image import BootstrappableImage

from .config import Config

if TYPE_CHECKING:
    from .images import ConfiguredImages, DistroImages


class ConfiguredImage(BootstrappableImage):
    """Image described in a configuration file."""

    images: "ConfiguredImages"

    def __init__(
        self, *, images: "ConfiguredImages", name: str, config: Config, variant_of: Optional["DistroImage"]
    ) -> None:
        if variant_of is None:
            distro = config.distro
        else:
            distro = variant_of.distro
        super().__init__(images=images, name=name, distro=distro, bootstrapped=False)
        self.config = config
        self.config.warn_unsupported_entries(self.logger)
        self.variant_of = variant_of

    @override
    def _forwards_users(self) -> list[str]:
        res = set(super()._forwards_users())
        res.update(self.config.parent.forwards_users)
        res.update(self.config.bootstrap_info.forward_users)
        return sorted(res)

    @override
    def _distro_package_list(self) -> set[str]:
        res = super()._distro_package_list()
        res.update(self.config.parent.distro_package_list)
        return res

    @override
    def _config_package_list(self) -> set[str]:
        res = super()._config_package_list()
        res.update(self.config.parent.config_package_list)
        res.update(self.config.bootstrap_info.packages)
        return res

    @override
    def _maintscripts(self) -> list[str]:
        res = super()._maintscripts()
        res.extend(self.config.parent.maintscripts)
        if self.config.bootstrap_info.maintscript:
            res.append(self.config.bootstrap_info.maintscript)
        return res

    @override
    def remove_config(self) -> None:
        self.logger.info("%s: removing image configuration file", self.config.path)
        self.config.path.unlink()


class DistroImage(BootstrappableImage):
    """Image described in a configuration file."""

    images: "DistroImages"

    @override
    def _distro_package_list(self) -> set[str]:
        res = super()._distro_package_list()
        res.update(self.distro.get_base_packages())
        return res

    @override
    def remove_config(self) -> None:
        pass
