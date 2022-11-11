from __future__ import annotations

import contextlib
import graphlib
import logging
import os
import shutil
import stat
import subprocess
from collections import defaultdict
from typing import TYPE_CHECKING, ContextManager, Generator, List, Optional

from .btrfs import Subvolume, do_dedupe, is_btrfs
from .distro import DistroFamily
from .system import MaintenanceSystem, System, SystemConfig
from .runner import LocalRunner

if TYPE_CHECKING:
    from .session import Session

log = logging.getLogger("images")

MACHINECTL_PATH = "/var/lib/machines"


class Images:
    """
    Image storage made available as a directory in the file system
    """
    def __init__(self, session: Session, imagedir: str):
        self.session = session
        self.imagedir = imagedir

    def list_images(self) -> List[str]:
        """
        List the names of images found in image directories
        """
        res = set()
        with os.scandir(self.imagedir) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue

                if entry.is_dir():
                    res.add(entry.name)
                elif entry.name.endswith(".yaml"):
                    res.add(entry.name[:-5])

        for path in self.session.moncic.config.imageconfdirs:
            with os.scandir(path) as it:
                for entry in it:
                    if entry.name.startswith(".") or entry.is_dir():
                        continue
                    if not entry.name.endswith(".yaml") or entry.name == "moncic-ci.yaml":
                        continue
                    res.add(entry.name[:-5])

        return sorted(res)

    def local_run(self, system_config: SystemConfig, cmd: List[str]) -> subprocess.CompletedProcess:
        """
        Run a command on the host system.
        """
        return LocalRunner.run(system_config.logger, cmd, system_config=system_config)

    def get_distro_tarball(self, distro_name: str) -> Optional[str]:
        """
        Return the path to a tarball that can be used to bootstrap a chroot for
        this system.

        Return None if no such tarball is present
        """
        for ext in ('.tar.gz', '.tar.xz', '.tar'):
            tarball_path = os.path.join(self.imagedir, distro_name + ext)
            if os.path.exists(tarball_path):
                return tarball_path
        return None

    def system(self, name: str) -> ContextManager[System]:
        """
        Instantiate a System that can only be used for the duration
        of this context manager.
        """
        raise NotImplementedError(f"{self.__class__.__name__}.maintenance_system is not implemented")

    def maintenance_system(self, name: str) -> ContextManager[MaintenanceSystem]:
        """
        Instantiate a MaintenanceSystem that can only be used for the duration
        of this context manager.

        This allows maintenance to be transactional, limited to backends that
        support it, so that errors in the maintenance roll back to the previous
        state and do not leave an inconsistent OS image
        """
        raise NotImplementedError(f"{self.__class__.__name__}.maintenance_system is not implemented")

    def bootstrap_system(self, name: str):
        """
        Bootstrap the given system if missing
        """
        raise NotImplementedError(f"{self.__class__.__name__}.bootstrap_system is not implemented")

    def remove_system(self, name: str):
        """
        Remove the named system if it exists
        """
        raise NotImplementedError(f"{self.__class__.__name__}.remove_system is not implemented")

    def find_config(self, name: str) -> Optional[str]:
        """
        Return the path of the config file of the given image, if it exists
        """
        # Import here to prevent import loops
        from .system import SystemConfig
        return SystemConfig.find_config(self.session.moncic.config, self.imagedir, name)

    def remove_config(self, name: str):
        """
        Remove the configuration for the named system, if it exists
        """
        # Import here to prevent import loops
        if path := self.find_config(name):
            log.info("%s: removing image configuration file", path)
            os.unlink(path)

    def add_dependencies(self, images: List[str]) -> List[str]:
        """
        Add dependencies to the given list of images, returning the extended
        list.

        The list returned is ordered by dependencies: if an image extends
        another, the base image is listed before those that depend on it.
        """
        # Import here to prevent import loops
        from .system import SystemConfig
        res: graphlib.TopologicalSorter = graphlib.TopologicalSorter()
        for name in images:
            config = SystemConfig.load(self.session.moncic.config, self.imagedir, name)
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
    @contextlib.contextmanager
    def system(self, name: str) -> Generator[System, None, None]:
        system_config = SystemConfig.load(self.session.moncic.config, self.imagedir, name)
        # Force using tmpfs backing for ephemeral containers, since we cannot
        # use snapshots
        system_config.tmpfs = True
        yield System(self, system_config)

    @contextlib.contextmanager
    def maintenance_system(self, name: str) -> Generator[MaintenanceSystem, None, None]:
        system_config = SystemConfig.load(self.session.moncic.config, self.imagedir, name)
        # Force using tmpfs backing for ephemeral containers, since we cannot
        # use snapshots
        system_config.tmpfs = True
        yield MaintenanceSystem(self, system_config)

    def bootstrap_system(self, name: str):
        system_config = SystemConfig.load(self.session.moncic.config, self.imagedir, name)
        if os.path.exists(system_config.path):
            return

        log.info("%s: bootstrapping directory", name)

        path = os.path.join(self.imagedir, name)
        work_path = path + ".new"
        system_config.path = work_path

        try:
            if system_config.extends is not None:
                with self.system(system_config.extends) as parent:
                    self.local_run(system_config, ["cp", "--reflink=auto", "-a", parent.path, work_path])
            else:
                tarball_path = self.get_distro_tarball(system_config.distro)
                if tarball_path is not None:
                    # Shortcut in case we have a chroot in a tarball
                    os.mkdir(work_path)
                    self.local_run(system_config, ["tar", "-C", work_path, "-axf", tarball_path])
                else:
                    system = MaintenanceSystem(self, system_config)
                    distro = DistroFamily.lookup_distro(system_config.distro)
                    distro.bootstrap(system)
        except BaseException:
            shutil.rmtree(work_path)
            raise
        else:
            if os.path.exists(work_path):
                os.rename(work_path, path)

    def remove_system(self, name: str):
        path = os.path.join(self.imagedir, name)
        if not os.path.exists(path):
            return
        shutil.rmtree(path)


class BtrfsImages(Images):
    """
    Images stored in a btrfs filesystem
    """
    @contextlib.contextmanager
    def system(self, name: str) -> Generator[System, None, None]:
        system_config = SystemConfig.load(self.session.moncic.config, self.imagedir, name)
        yield System(self, system_config)

    @contextlib.contextmanager
    def maintenance_system(self, name: str) -> Generator[MaintenanceSystem, None, None]:
        system_config = SystemConfig.load(self.session.moncic.config, self.imagedir, name)
        path = os.path.join(self.imagedir, name)
        work_path = path + ".new"
        if os.path.exists(work_path):
            raise RuntimeError(f"Found existing {work_path} which should be removed")
        system_config.path = work_path
        if not os.path.exists(path):
            # Transactional work on a new path
            try:
                yield MaintenanceSystem(self, system_config)
            except BaseException:
                # TODO: remove work_path is currently not needed as System is
                #       doing it. Maybe move that here?
                raise
            else:
                if os.path.exists(work_path):
                    os.rename(work_path, path)
        else:
            # Update
            subvolume = Subvolume(system_config, self.session.moncic.config)
            # Create work_path as a snapshot of path
            subvolume.snapshot(path)
            try:
                yield MaintenanceSystem(self, system_config)
            except BaseException:
                system_config.logger.warning("Rolling back maintenance changes")
                subvolume.remove()
                raise
            else:
                system_config.logger.info("Committing maintenance changes")
                # Swap and remove
                subvolume.replace_subvolume(path)

    def bootstrap_system(self, name: str):
        system_config = SystemConfig.load(self.session.moncic.config, self.imagedir, name)
        if os.path.exists(system_config.path):
            return

        log.info("%s: bootstrapping subvolume", name)

        path = os.path.join(self.imagedir, name)
        work_path = path + ".new"
        system_config.path = work_path

        try:
            if system_config.extends is not None:
                with self.system(system_config.extends) as parent:
                    subvolume = Subvolume(system_config, self.session.moncic.config)
                    subvolume.snapshot(parent.path)
            else:
                tarball_path = self.get_distro_tarball(system_config.distro)
                subvolume = Subvolume(system_config, self.session.moncic.config)
                with subvolume.create():
                    if tarball_path is not None:
                        # Shortcut in case we have a chroot in a tarball
                        self.local_run(system_config, ["tar", "-C", work_path, "-axf", tarball_path])
                    else:
                        system = MaintenanceSystem(self, system_config)
                        distro = DistroFamily.lookup_distro(system_config.distro)
                        distro.bootstrap(system)
        except BaseException:
            # TODO: remove work_path is currently not needed as System is
            #       doing it. Maybe move that here?
            raise
        else:
            if os.path.exists(work_path):
                os.rename(work_path, path)

    def remove_system(self, name: str):
        system_config = SystemConfig.load(self.session.moncic.config, self.imagedir, name)
        subvolume = Subvolume(system_config, self.session.moncic.config)
        subvolume.remove()

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
                for (dirpath, dirnames, filenames, dirfd) in os.fwalk(path):
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
                saved += do_dedupe(
                        os.path.join(imagedir, images[0], name),
                        os.path.join(imagedir, imgname, name),
                        size)
            # if saved > 0:
            #     log.info("%s: found in %s, recovered %db", name, ", ".join(images), saved)
            total_saved += saved

        log.info("%d total bytes are currently deduplicated", total_saved)


class ImageStorage:
    """
    Interface for handling image storage
    """
    def __init__(self, session: Session):
        self.session = session

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        """
        Make the image storage available as a directory, for the duration of
        this context manager
        """
        raise NotImplementedError(f"{self.__class__.__name__}.imagedir is not implemented")

    @classmethod
    def create(cls, session: Session, path: str) -> "ImageStorage":
        """
        Instantiate the right ImageStorage for a path
        """
        if path == MACHINECTL_PATH:
            if is_btrfs(path):
                return BtrfsMachineImageStorage(session)
            else:
                return PlainMachineImageStorage(session)
        elif os.path.isdir(path):
            if is_btrfs(path):
                return BtrfsImageStorage(session, path)
            else:
                return PlainImageStorage(session, path)
        else:
            raise RuntimeError(f"images path {path!r} does not point to a directory")

    @classmethod
    def create_default(cls, session: Session) -> "ImageStorage":
        """
        Instantiate a default ImageStorage in case no path has been provided
        """
        return cls.create(session, MACHINECTL_PATH)


class PlainImageStorage(ImageStorage):
    """
    Store images in a non-btrfs directory
    """
    def __init__(self, session: Session, imagedir: str):
        super().__init__(session)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        yield PlainImages(self.session, self.imagedir)


class BtrfsImageStorage(ImageStorage):
    """
    Store images in a btrfs directory
    """
    def __init__(self, session: Session, imagedir: str):
        super().__init__(session)
        self.imagedir = imagedir

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        yield BtrfsImages(self.session, self.imagedir)


class PlainMachineImageStorage(PlainImageStorage):
    """
    Store images in /var/lib/machines in a way that is compatibile with
    machinectl
    """
    def __init__(self, session: Session):
        super().__init__(session, MACHINECTL_PATH)

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        yield Images(self.session, self.imagedir)


class BtrfsMachineImageStorage(BtrfsImageStorage):
    """
    Store images in /var/lib/machines in a way that is compatibile with
    machinectl
    """
    def __init__(self, session: Session):
        super().__init__(session, MACHINECTL_PATH)

    @contextlib.contextmanager
    def images(self) -> Generator[Images, None, None]:
        yield BtrfsImages(self.session, self.imagedir)
