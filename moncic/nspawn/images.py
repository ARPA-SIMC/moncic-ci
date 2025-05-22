from __future__ import annotations

import abc
import contextlib
import graphlib
import shutil
import logging
import os
import subprocess
import stat
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, ContextManager, override, Generator

from moncic.runner import LocalRunner
from moncic.image import BootstrappableImage, RunnableImage, Image
from moncic.images import BootstrappingImages
from moncic.utils.btrfs import do_dedupe
from moncic.utils.btrfs import Subvolume
from moncic.context import privs
from moncic.distro import Distro, DistroFamily

from .image import NspawnImage, NspawnImageBtrfs, NspawnImagePlain

if TYPE_CHECKING:
    from moncic.session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = "/var/lib/machines"


class NspawnImages(BootstrappingImages, abc.ABC):
    """
    Image storage made available as a directory in the file system
    """

    def __init__(self, session: Session, imagedir: Path):
        self.session = session
        self.imagedir = imagedir
        self.logger = logging.getLogger("images.nspawn")

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
            if any(x.exists() for x in (path / name, path / f"{name}.yaml")):
                return True
        return False

    @override
    def list_images(self) -> list[str]:
        images: list[str]
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
        for ext in (".tar.gz", ".tar.xz", ".tar"):
            tarball_path = self.imagedir / (distro.name + ext)
            if tarball_path.exists():
                return tarball_path
        return None

    def add_dependencies(self, images: list[str]) -> list[str]:
        """
        Add dependencies to the given list of images, returning the extended
        list.

        The list returned is ordered by dependencies: if an image extends
        another, the base image is listed before those that depend on it.
        """
        res: graphlib.TopologicalSorter = graphlib.TopologicalSorter()
        for name in images:
            image = self.image(name)
            if image.bootstrap_info.extends is not None:
                res.add(image.name, image.bootstrap_info.extends)
            else:
                res.add(image.name)
        return list(res.static_order())

    def _find_distro(self, path: Path) -> Distro:
        try:
            return DistroFamily.from_path(path)
        except PermissionError:
            if not privs.can_regain():
                raise
            with privs.root():
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
        with privs.root():
            from moncic.provision.image import ConfiguredImage, DistroImage

            path = self.imagedir / image.name
            if path.exists():
                return self.image(image.name)

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

            return self.image(image.name)


class PlainImages(NspawnImages):
    """
    Images stored in a non-btrfs filesystem
    """

    @override
    def image(self, name: str) -> NspawnImage:
        path = (self.imagedir / name).absolute()
        distro = self._find_distro(path)
        return NspawnImagePlain(images=self, name=name, distro=distro, path=path)

    @override
    @contextlib.contextmanager
    def transactional_workdir(self, path: Path, compression: str | None) -> Generator[Path, None, None]:
        if path.exists():
            logging.info("%s: transactional updates on non-btrfs nspawn images are not supported", path)
            yield path
        else:
            work_path = path.parent / f"{path.name}.new"
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
        LocalRunner.run(logger=self.logger, cmd=["cp", "--reflink=auto", "-a", image.path.as_posix(), path.as_posix()])

    @override
    def _bootstrap_new(self, distro: Distro, path: Path, compression: str | None) -> None:
        tarball_path = self.get_distro_tarball(distro)
        if tarball_path is not None:
            # Shortcut in case we have a chroot in a tarball
            path.mkdir()
            LocalRunner.run(logger=self.logger, cmd=["tar", "-C", path.as_posix(), "-axf", tarball_path.as_posix()])
        else:
            distro.bootstrap(path)


class BtrfsImages(NspawnImages):
    """
    Images stored in a btrfs filesystem
    """

    @override
    def image(self, name: str) -> NspawnImage:
        path = (self.imagedir / name).absolute()
        distro = self._find_distro(path)
        return NspawnImageBtrfs(images=self, name=name, distro=distro, path=path)

    @override
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

    @override
    @contextlib.contextmanager
    def transactional_workdir(self, path: Path, compression: str | None) -> Generator[Path, None, None]:
        work_path = path.parent / f"{path.name}.new"
        subvolume = Subvolume(self.session.moncic.config, work_path, compression)
        if not path.exists():
            with subvolume.create():
                try:
                    yield work_path
                except BaseException:
                    subvolume.remove()
                    raise
                else:
                    work_path.rename(path)
        else:
            subvolume = Subvolume(self.session.moncic.config, work_path, compression)
            # Create work_path as a snapshot of path
            subvolume.snapshot(path)
            try:
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
            LocalRunner.run(logger=self.logger, cmd=["tar", "-C", path.as_posix(), "-axf", tarball_path.as_posix()])
        else:
            distro.bootstrap(path)


class MachinectlImages(NspawnImages):
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
