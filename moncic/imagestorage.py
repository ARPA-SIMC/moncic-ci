from __future__ import annotations

import contextlib
import graphlib
import logging
import os
import stat
import subprocess
import tempfile
from collections import defaultdict
from typing import TYPE_CHECKING, Generator, List

from .btrfs import do_dedupe, is_btrfs
from .system import System, SystemConfig
from .utils import is_on_rotational, pause_automounting

if TYPE_CHECKING:
    from .moncic import Moncic

log = logging.getLogger("images")

MACHINECTL_PATH = "/var/lib/machines"


class Images:
    """
    Image storage made available as a directory in the file system
    """
    def __init__(self, moncic: Moncic, imagedir: str):
        self.moncic = moncic
        self.imagedir = imagedir

    def list_images(self) -> List[str]:
        """
        List the names of images found in image directories
        """
        res = set()
        for entry in os.scandir(self.imagedir):
            if entry.name.startswith("."):
                continue

            if entry.is_dir():
                res.add(entry.name)
            elif entry.name.endswith(".yaml"):
                res.add(entry.name[:-5])
        return sorted(res)

    def create_system(self, name: str) -> System:
        """
        Instantiate a System from its name or path
        """
        raise NotImplementedError(f"{self.__class__.__name__}.create_system is not implemented")

    def add_dependencies(self, images: List[str]) -> List[str]:
        """
        Add dependencies to the given list of images, returning the extended
        list.

        The list returned is ordered by dependencies: if an image extends
        another, the base image is listed before those that depend on it.
        """
        # Import here to prevent import loops
        from .system import SystemConfig
        res = graphlib.TopologicalSorter()
        for name in images:
            config = SystemConfig.load(os.path.join(self.imagedir, name))
            if config.extends is not None:
                res.add(config.name, config.extends)
            else:
                res.add(config.name)

        return list(res.static_order())

    def deduplicate(self):
        pass


class PlainImages(Images):
    """
    Images stored in a non-btrfs filesystem
    """
    def create_system(self, name: str) -> System:
        system_config = SystemConfig.load(os.path.join(self.imagedir, name))
        # Force using tmpfs backing for ephemeral containers, since we cannot
        # use snapshots
        system_config.tmpfs = True
        return self.moncic.system_class(self, system_config)


class BtrfsImages(Images):
    """
    Images stored in a btrfs filesystem
    """
    def create_system(self, name: str) -> System:
        system_config = SystemConfig.load(os.path.join(self.imagedir, name))
        return self.moncic.system_class(self, system_config)

    def deduplicate(self):
        """
        Attempt deduplicating files that have the same name and size across OS
        images
        """
        super().deduplicate()
        log.info("Deduplicating disk usage...")

        imagedir = self.imagedir

        by_name_size = defaultdict(list)
        for entry in os.scandir(imagedir):
            if entry.name.startswith("."):
                continue
            if not entry.is_dir():
                continue

            path = os.path.join(imagedir, entry.name)
            for (dirpath, dirnames, filenames, dirfd) in os.fwalk(path):
                relpath = os.path.relpath(dirpath, path)
                for fn in filenames:
                    st = os.lstat(fn, dir_fd=dirfd)
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    size = st.st_size
                    by_name_size[(os.path.join(relpath, fn), size)].append(entry.name)

        with self.moncic.privs.root():
            total_saved = 0
            for (name, size), images in by_name_size.items():
                if len(images) < 2:
                    continue
                saved = 0
                for imgname in images[1:]:
                    saved += do_dedupe(
                            os.path.join(imagedir, images[0], name),
                            os.path.join(imagedir, imgname, name),
                            size)
                # if saved > 0:
                #     log.info("%s: found in %s, recovered %db", name, ", ".join(images), saved)
                total_saved += saved

        log.info("%d total bytes are currently deduplicated", total_saved)


class ImagesInFile(BtrfsImages):
    """
    Images stored in a file
    """
    def __init__(self, storage: "FileImageStorage", imagedir: str):
        super().__init__(storage.moncic, imagedir)
        self.storage = storage

    def deduplicate(self):
        super().deduplicate()

        if self.storage.should_trim():
            log.info("%s: trimming unused storage", self.storage.imagefile)
            with self.moncic.privs.root():
                subprocess.run(["fstrim", self.imagedir], check=True)


class ImageStorage:
    """
    Interface for handling image storage
    """
    def __init__(self, moncic: Moncic):
        self.moncic = moncic

    @contextlib.contextmanager
    def images(self) -> Generator[Images]:
        """
        Make the image storage available as a directory, for the duration of
        this context manager
        """
        raise NotImplementedError(f"{self.__class__.__name__}.imagedir is not implemented")

    @classmethod
    def create(cls, moncic: Moncic, path: str) -> "ImageStorage":
        """
        Instantiate the right ImageStorage for a path
        """
        if path == MACHINECTL_PATH:
            if is_btrfs(path):
                return BtrfsMachineImageStorage(moncic)
            else:
                return PlainMachineImageStorage(moncic)
        elif os.path.isdir(path):
            if is_btrfs(path):
                return BtrfsImageStorage(moncic, path)
            else:
                return PlainImageStorage(moncic, path)
        else:
            return FileImageStorage(moncic, path)

    @classmethod
    def create_default(cls, moncic: Moncic) -> "ImageStorage":
        """
        Instantiate a default ImageStorage in case no path has been provided
        """
        return cls.create(MACHINECTL_PATH)


class PlainImageStorage(ImageStorage):
    """
    Store images in a non-btrfs directory
    """
    def __init__(self, moncic: Moncic, imagedir: str):
        super().__init__(moncic)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator[Images]:
        yield PlainImages(self.moncic, self.imagedir)


class BtrfsImageStorage(ImageStorage):
    """
    Store images in a btrfs directory
    """
    def __init__(self, moncic: Moncic, imagedir: str):
        super().__init__(moncic)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator[Images]:
        yield BtrfsImages(self.moncic, self.imagedir)


class FileImageStorage(ImageStorage):
    """
    Store images in a btrfs filesystem on a file
    """
    def __init__(self, moncic: Moncic, imagefile: str):
        super().__init__(moncic)
        self.imagefile = imagefile

    @contextlib.contextmanager
    def images(self) -> Generator[Images]:
        with tempfile.TemporaryDirectory() as imagedir:
            with self.moncic.privs.root():
                with pause_automounting(self.imagefile):
                    subprocess.run(["mount", self.imagefile, imagedir], check=True)

                    try:
                        with self.moncic.privs.user():
                            yield ImagesInFile(self, imagedir)
                    finally:
                        subprocess.run(["umount", imagedir], check=True)

    def should_trim(self):
        """
        Run fstrim on the image file if requested by config or if we can see
        that the image file is on a SSD
        """
        do_trim = self.moncic.config.trim_image_file
        if do_trim is None:
            rot = is_on_rotational(self.imagefile)
            if rot or rot is None:
                return False
        elif not do_trim:
            return False

        return True


class PlainMachineImageStorage(PlainImageStorage):
    """
    Store images in /var/lib/machines in a way that is compatibile with
    machinectl
    """
    def __init__(self, moncic: Moncic):
        super().__init__(moncic, MACHINECTL_PATH)

    @contextlib.contextmanager
    def images(self) -> Generator[Images]:
        yield Images(self.moncic, self.imagedir)


class BtrfsMachineImageStorage(BtrfsImageStorage):
    """
    Store images in /var/lib/machines in a way that is compatibile with
    machinectl
    """
    def __init__(self, moncic: Moncic):
        super().__init__(moncic, MACHINECTL_PATH)

    @contextlib.contextmanager
    def images(self) -> Generator[Images]:
        yield BtrfsImages(self.moncic, self.imagedir)
