from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, override

from moncic.distro import DistroFamily
from moncic.images import Images

from .config import Config

if TYPE_CHECKING:
    from moncic.image import Image

log = logging.getLogger("images")


class ConfiguredImages(Images):
    """Images described by configuration files."""

    @cached_property
    def configs(self) -> dict[str, Path]:
        """Return a dict mapping image names to configuration files."""
        configured: dict[str, Path] = {}
        for path in self.session.moncic.config.imageconfdirs:
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
    def image(self, name: str) -> Image:
        from .image import ConfiguredImage

        if path := self.configs.get(name):
            config = Config(self.session, name, path)
            return ConfiguredImage(session=self.session, name=name, config=config)

        raise KeyError(f"Image {name!r} not found")


class DistroImages(Images):
    """Images described by Moncic-CI distribution database."""

    @override
    def list_images(self) -> list[str]:
        images: list[str] = []
        for family in DistroFamily.list_families():
            for distro_info in family.list_distros():
                images.append(distro_info.name)
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
    def image(self, name: str) -> Image:
        from .image import DistroImage

        distro = DistroFamily.lookup_distro(name)
        return DistroImage(session=self.session, name=name, distro=distro)
