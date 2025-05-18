from __future__ import annotations

import abc
import graphlib
import logging
import os
import stat
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, override

from moncic.utils.btrfs import do_dedupe
from moncic.images import Images
from moncic.image import Image
from .image import NspawnImage, NspawnImagePlain, NspawnImageBtrfs

if TYPE_CHECKING:
    from moncic.session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = "/var/lib/machines"


class NspawnImages(Images, abc.ABC):
    """
    Image storage made available as a directory in the file system
    """

    def __init__(self, session: Session, imagedir: Path):
        self.session = session
        self.imagedir = imagedir

    @abc.abstractmethod
    @override
    def image(self, name: str) -> NspawnImage:
        """
        Return the configuration for the named system
        """

    @override
    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        for path in self.session.moncic.config.imageconfdirs:
            if any((x.exists() for x in (path / name, path / f"{name}.yaml"))):
                return True
        return False

    def list_images(self) -> list[Image]:
        """
        List the names of images found in image directories
        """
        res = set()
        for path in self.session.moncic.config.imageconfdirs:
            with os.scandir(path) as it:
                for entry in it:
                    if entry.name.startswith(".") or entry.is_dir():
                        continue
                    if not entry.name.endswith(".yaml") or entry.name == "moncic-ci.yaml":
                        continue
                    res.add(entry.name[:-5])
        return [self.image(name) for name in sorted(res)]

    def get_distro_tarball(self, distro_name: str) -> str | None:
        """
        Return the path to a tarball that can be used to bootstrap a chroot for
        this system.

        Return None if no such tarball is present
        """
        for ext in (".tar.gz", ".tar.xz", ".tar"):
            tarball_path = os.path.join(self.imagedir, distro_name + ext)
            if os.path.exists(tarball_path):
                return tarball_path
        return None

    def add_dependencies(self, images: list[str]) -> list[str]:
        """
        Add dependencies to the given list of images, returning the extended
        list.

        The list returned is ordered by dependencies: if an image extends
        another, the base image is listed before those that depend on it.
        """
        # Import here to prevent import loops
        from .system import NspawnImage

        res: graphlib.TopologicalSorter = graphlib.TopologicalSorter()
        for name in images:
            image = self.image(name)
            if image.extends is not None:
                res.add(image.name, image.extends)
            else:
                res.add(image.name)

        return list(res.static_order())

    def deduplicate(self):
        pass


class PlainImages(NspawnImages):
    """
    Images stored in a non-btrfs filesystem
    """

    @override
    def image(self, name: str) -> NspawnImage:
        image = NspawnImagePlain.load(self.session.moncic.config, self, name)
        # Force using tmpfs backing for ephemeral containers, since we cannot
        # use snapshots
        image.tmpfs = True
        return image


class BtrfsImages(NspawnImages):
    """
    Images stored in a btrfs filesystem
    """

    @override
    def image(self, name: str) -> NspawnImage:
        return NspawnImageBtrfs.load(self.session.moncic.config, self, name)

    def deduplicate(self):
        """
        Attempt deduplicating files that have the same name and size across OS
        images
        """
        super().deduplicate()
        log.info("Deduplicating disk usage...")

        imagedir = self.imagedir

        by_name_size = defaultdict(list)
        with os.scandir(imagedir) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                if not entry.is_dir():
                    continue

                path = os.path.join(imagedir, entry.name)
                for dirpath, dirnames, filenames, dirfd in os.fwalk(path):
                    relpath = os.path.relpath(dirpath, path)
                    for fn in filenames:
                        st = os.lstat(fn, dir_fd=dirfd)
                        if not stat.S_ISREG(st.st_mode):
                            continue
                        size = st.st_size
                        by_name_size[(os.path.join(relpath, fn), size)].append(entry.name)

        total_saved = 0
        for (name, size), images in by_name_size.items():
            if len(images) < 2:
                continue
            saved = 0
            for imgname in images[1:]:
                saved += do_dedupe(os.path.join(imagedir, images[0], name), os.path.join(imagedir, imgname, name), size)
            # if saved > 0:
            #     log.info("%s: found in %s, recovered %db", name, ", ".join(images), saved)
            total_saved += saved

        log.info("%d total bytes are currently deduplicated", total_saved)
