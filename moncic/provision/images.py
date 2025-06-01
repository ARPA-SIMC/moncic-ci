from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, override

from moncic.distro import DistroFamily
from moncic.images import Images
from moncic.image import Image

from .config import Config

if TYPE_CHECKING:
    from moncic.image import BootstrappableImage

log = logging.getLogger("images")


class ConfiguredImages(Images):
    """Images described by configuration files."""

    @override
    def get_logger(self) -> logging.Logger:
        return logging.getLogger("images.configured")

    @override
    def reload(self) -> None:
        self.__dict__.pop("configs", None)

    @cached_property
    def configs(self) -> dict[str, Path]:
        """Return a dict mapping image names to configuration files."""
        configured: dict[str, Path] = {}
        for path in self.session.moncic.config.imageconfdirs:
            if not path.is_dir():
                continue
            for f in path.iterdir():
                if f.name.startswith(".") or f.is_dir():
                    continue
                if f.suffix not in (".yml", ".yaml"):
                    continue
                if f.stem == "moncic-ci":
                    continue
                configured[f.stem] = f
        return configured

    @override
    def list_images(self) -> list[str]:
        return sorted(self.configs.keys())

    @override
    def has_image(self, name: str) -> bool:
        return name in self.configs

    @override
    def image(self, name: str, variant_of: Image | None = None) -> BootstrappableImage:
        from .image import ConfiguredImage

        if not isinstance(variant_of, DistroImages):
            raise NotImplementedError(
                f"Image {name!r} is configured from {variant_of!r} which is a "
                f" {variant_of.__class__.__name__} instead of DistroImage"
            )

        if path := self.configs.get(name):
            config = Config(self.session, name, path)
            return ConfiguredImage(images=self, name=name, config=config, variant_of=variant_of)

        raise KeyError(f"Image {name!r} not found")


class DistroImages(Images):
    """Images described by Moncic-CI distribution database."""

    @override
    def get_logger(self) -> logging.Logger:
        return logging.getLogger("images.distro")

    @override
    def list_images(self) -> list[str]:
        images: list[str] = []
        for family in DistroFamily.list_families():
            for distro in family.distros:
                images.append(distro.full_name)
        images.sort()
        return images

    @override
    def has_image(self, name: str) -> bool:
        try:
            DistroFamily.lookup_distro(name)
            return True
        except KeyError:
            return False

    @override
    def image(self, name: str, variant_of: Image | None = None) -> BootstrappableImage:
        from .image import DistroImage

        if variant_of is not None:
            raise NotImplementedError("cannot build a DistroImage as an instance of another image")

        distro = DistroFamily.lookup_distro(name)
        return DistroImage(images=self, name=name, distro=distro)
