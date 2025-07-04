from __future__ import annotations

import abc
import contextlib
import logging
import os
import re
import shutil
import stat
import subprocess
from collections import defaultdict
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, ContextManager, override

from moncic import context
from moncic.distro import Distro, DistroFamily
from moncic.image import BootstrappableImage, Image, RunnableImage
from moncic.images import BootstrappingImages
from moncic.utils.btrfs import Subvolume, do_dedupe

from .image import NspawnImage, NspawnImageBtrfs, NspawnImagePlain

if TYPE_CHECKING:
    from moncic.session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = Path("/var/lib/machines")


class NspawnImages(BootstrappingImages, abc.ABC):
    """
    Image storage made available as a directory in the file system
    """

    image_class: type[NspawnImage]

    def __init__(self, session: Session, imagedir: Path):
        self.session = session
        self.imagedir = imagedir
        self.logger = logging.getLogger("images.nspawn")

    @classmethod
    @abc.abstractmethod
    def create_machinectl(cls, session: Session) -> NspawnImages:
        """Create a NspawnImages accessing machinectl storage."""

    @override
    def get_logger(self) -> logging.Logger:
        return logging.getLogger("images.nspawn")

    @override
    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        return (self.imagedir / name).is_dir()

    @override
    def image(self, name: str, variant_of: Image | None = None) -> RunnableImage:
        path = (self.imagedir / name).absolute()
        with context.privs.root():
            if not path.is_dir():
                raise KeyError(f"Image {name!r} not found")
        bootstrapped_from: BootstrappableImage | None = None
        match variant_of:
            case None:
                distro = self._find_distro(path)
            case BootstrappableImage():
                distro = variant_of.distro
                bootstrapped_from = variant_of
            case RunnableImage():
                # Reuse the previous found runnable image
                return variant_of
            case _:
                raise NotImplementedError(f"variant_of has unknown image type {variant_of.__class__.__name__}")

        return self.image_class(images=self, name=name, distro=distro, path=path, bootstrapped_from=bootstrapped_from)

    @override
    def list_images(self) -> list[str]:
        images: list[str] = []
        for path in self.imagedir.iterdir():
            if path.name.startswith(".") or not path.is_dir():
                continue
            images.append(path.name)
        images.sort()
        return images

    def get_distro_tarball(self, distro: Distro) -> Path | None:
        """
        Return the path to a tarball that can be used to bootstrap a chroot for
        this system.

        Return None if no such tarball is present
        """
        re_tarball = re.compile(r"^(.+)(?:\.tar|\.tar\.gz|\.tar\.xz)")
        with context.privs.root():
            for path in self.imagedir.iterdir():
                if mo := re_tarball.match(path.name):
                    name = mo.group(1)
                else:
                    continue
                try:
                    found = DistroFamily.lookup_distro(name)
                except KeyError:
                    continue
                if found.name == distro.name:
                    return path
        return None

    def _find_distro(self, path: Path) -> Distro:
        try:
            return DistroFamily.from_path(path)
        except PermissionError:
            if not context.privs.can_regain():
                raise
            with context.privs.root():
                return DistroFamily.from_path(path)

    @abc.abstractmethod
    def transactional_workdir(self, path: Path, compression: str | None) -> ContextManager[Path]:
        """Create a working directory for transactional maintenance of the image at path."""

    @abc.abstractmethod
    def _extend_parent(self, image: Image, path: Path, compression: str | None) -> None:
        """Initialize self.path with a clone of the parent image."""

    @abc.abstractmethod
    def _bootstrap_new(self, distro: Distro, path: Path, compression: str | None) -> None:
        """Bootstrap a new OS image from scratch."""

    @override
    def bootstrap(self, image: BootstrappableImage) -> RunnableImage:
        with context.privs.root():
            from moncic.provision.image import ConfiguredImage, DistroImage

            path = self.imagedir / image.name
            if path.exists():
                return self.image(image.name, variant_of=image)

            image.logger.info("bootstrapping into %s", path)

            match image:
                case ConfiguredImage():
                    compression = image.config.bootstrap_info.compression
                    with self.transactional_workdir(path, compression=compression) as work_path:
                        self._extend_parent(image.config.parent, work_path, compression=compression)
                case DistroImage():
                    compression = self.session.moncic.config.compression
                    with self.transactional_workdir(path, compression=compression) as work_path:
                        self._bootstrap_new(image.distro, work_path, compression=compression)
                case _:
                    raise NotImplementedError

            return self.image(image.name, variant_of=image)


class PlainImages(NspawnImages):
    """
    Images stored in a non-btrfs filesystem
    """

    image_class = NspawnImagePlain

    @override
    @classmethod
    def create_machinectl(cls, session: Session) -> NspawnImages:
        return PlainMachinectlImages(session)

    @override
    @contextlib.contextmanager
    def transactional_workdir(self, path: Path, compression: str | None) -> Generator[Path]:
        if path.exists():
            logging.info("%s: transactional updates on non-btrfs nspawn images are not supported", path)
            yield path
        else:
            work_path = path.parent / f"{path.name}.new"
            if work_path.exists():
                # Remove an old work path left around
                shutil.rmtree(work_path)
            try:
                yield work_path
            except BaseException:
                if work_path.exists():
                    shutil.rmtree(work_path)
                raise
            else:
                if work_path.exists():
                    work_path.rename(path)

    @override
    def _extend_parent(self, image: Image, path: Path, compression: str | None) -> None:
        from moncic.provision.image import ConfiguredImage, DistroImage

        match image:
            case DistroImage():
                return self._bootstrap_new(image.distro, path, compression)
            case ConfiguredImage():
                image = image.bootstrap()
            case NspawnImage():
                pass
            case _:
                raise NotImplementedError(f"Cannot extend image of type {image.__class__}")
        assert isinstance(image, NspawnImage)
        image.host_run(["cp", "--reflink=auto", "-a", image.path.as_posix(), path.as_posix()])

    @override
    def _bootstrap_new(self, distro: Distro, path: Path, compression: str | None) -> None:
        tarball_path = self.get_distro_tarball(distro)
        if tarball_path is not None:
            with context.privs.root():
                # Shortcut in case we have a chroot in a tarball
                path.mkdir()
                self.host_run(["tar", "-C", path.as_posix(), "-axf", tarball_path.as_posix()])
        else:
            with context.privs.root():
                distro.bootstrap(self, path)


class BtrfsImages(NspawnImages):
    """
    Images stored in a btrfs filesystem
    """

    image_class = NspawnImageBtrfs

    @override
    @classmethod
    def create_machinectl(cls, session: Session) -> NspawnImages:
        return BtrfsMachinectlImages(session)

    @override
    def deduplicate(self) -> None:
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

    @override
    @contextlib.contextmanager
    def transactional_workdir(self, path: Path, compression: str | None) -> Generator[Path]:
        work_path = path.parent / f"{path.name}.new"
        with context.privs.root():
            subvolume = Subvolume(self.session.moncic.config, work_path, compression)
            if work_path.exists():
                subvolume.remove()
                shutil.rmtree(work_path)
            if not path.exists():
                with subvolume.create():
                    try:
                        with context.privs.user():
                            yield work_path
                    except BaseException:
                        subvolume.remove()
                        raise
                    else:
                        work_path.rename(path)
            else:
                # Create work_path as a snapshot of path
                subvolume.snapshot(path)
                try:
                    with context.privs.user():
                        yield work_path
                except BaseException:
                    subvolume.remove()
                    raise
                else:
                    subvolume.replace_subvolume(path)

    @override
    def _extend_parent(self, image: Image, path: Path, compression: str | None) -> None:
        from moncic.provision.image import ConfiguredImage, DistroImage

        match image:
            case DistroImage():
                return self._bootstrap_new(image.distro, path, compression)
            case ConfiguredImage():
                image = image.bootstrap()
            case NspawnImage():
                pass
            case _:
                raise NotImplementedError(f"Cannot extend image of type {image.__class__}")
        assert isinstance(image, NspawnImage)
        subvolume = Subvolume(self.session.moncic.config, path, compression)
        subvolume.snapshot(image.path)

    @override
    def _bootstrap_new(self, distro: Distro, path: Path, compression: str | None) -> None:
        tarball_path = self.get_distro_tarball(distro)
        if tarball_path is not None:
            # Shortcut in case we have a chroot in a tarball
            with context.privs.root():
                self.host_run(cmd=["tar", "-C", path.as_posix(), "-axf", tarball_path.as_posix()])
        else:
            with context.privs.root():
                distro.bootstrap(self, path)


class MachinectlImages(NspawnImages):
    def __init__(self, session: Session) -> None:
        super().__init__(session, MACHINECTL_PATH)

    def _list_machines(self) -> set[str]:
        res = subprocess.run(
            ["machinectl", "list-images", "--no-pager", "--no-legend"], check=True, stdout=subprocess.PIPE, text=True
        )
        names: set[str] = set()
        for line in res.stdout.splitlines():
            names.add(line.split()[0])
        return names

    @override
    def has_image(self, name: str) -> bool:
        """Check if the named image exists."""
        return name in self._list_machines()

    @override
    def list_images(self) -> list[str]:
        return sorted(self._list_machines())


class PlainMachinectlImages(MachinectlImages, PlainImages):
    pass


class BtrfsMachinectlImages(MachinectlImages, BtrfsImages):
    pass
