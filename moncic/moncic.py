from __future__ import annotations
from collections import defaultdict
import contextlib
import dataclasses
import graphlib
import logging
import os
import stat
import subprocess
import tempfile
from typing import List, Optional, Type, TYPE_CHECKING

import yaml

from .privs import ProcessPrivs
from .utils import pause_automounting, is_on_rotational

if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)


@dataclasses.dataclass
class MoncicConfig:
    """
    Global Moncic-CI configuration
    """
    # Directory where images are stored
    imagedir: str = os.path.abspath("./images")
    # Btrfs compression level to set on OS image subvolumes when they are
    # created. The value is the same as can be set by `btrfs property set
    # compression`. Default: nothing is set
    compression: Optional[str] = None
    # If set to True, automatically run fstrim on the image file after regular
    # maintenance. If set to False, do not do that. By default, Moncic-CI will
    # run fstrim if it can detect that the image file is on a SSD
    trim_image_file: Optional[bool] = None

    @classmethod
    def find_git_dir(cls) -> Optional[str]:
        try:
            res = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True, check=True)
        except FileNotFoundError:
            # Handle the rare case where git is missing
            return None
        path = res.stdout.strip()
        if path:
            return path
        return None

    @classmethod
    def find_config_file(cls) -> Optional[str]:
        """
        Locate a moncic-ci.yaml configuration file in a list of well known
        directories
        """
        # Look for moncic-ci.yaml in the .git directory of the current
        # repository.
        #
        # We look inside the .git directory instead of the working directory
        # for two reasons:
        #  - to avoid cloning a repository that contains a Moncic-CI
        #    configuration, and then unexpectedly use it while running the
        #    `monci` command with sudo (dangerous!)
        #  - to allow to configure a local development setup without risking
        #    that it accidentally gets committed
        git_dir = cls.find_git_dir()
        if git_dir is not None:
            candidate = os.path.join(git_dir, "moncic-ci.yaml")
            if os.path.exists(candidate):
                return candidate

        # Try in the home directory, as ~/.config/moncic-ci/moncic-ci.yaml
        config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        local_config = os.path.join(config_home, "moncic-ci", "moncic-ci.yaml")
        if os.path.exists(local_config):
            return local_config

        # Try system-wide, as /etc/moncic-ci.yaml
        system_config = "/etc/moncic-ci.yaml"
        if os.path.exists(system_config):
            return system_config

        return None

    @classmethod
    def load(cls, path: Optional[str] = None):
        """
        Load the configuration from the given path, or from a list of default paths.
        """
        if path is None:
            path = cls.find_config_file()
        if path is None:
            # If no config file is loaded, keep the defaults
            return cls()

        try:
            with open(path, "rt") as fd:
                conf = yaml.load(fd, Loader=yaml.CLoader)
            log.info("Configuration loaded from %s", path)
        except FileNotFoundError:
            conf = None

        # Allow to use ~ in config files
        imagedir = conf.pop("imagedir", None)
        if imagedir is not None:
            conf["imagedir"] = os.path.expanduser(imagedir)

        return cls(**conf)


class Moncic:
    """
    General state of the Moncic-CI setup
    """
    def __init__(
            self,
            config: Optional[MoncicConfig] = None,
            privs: Optional[ProcessPrivs] = None,
            system_class: Optional[Type[System]] = None):
        # Import here to prevent import loops
        from .system import System

        self.privs: ProcessPrivs
        if privs is None:
            self.privs = ProcessPrivs()
        else:
            self.privs = privs

        # Drop privileges right away
        self.privs.drop()

        if config is None:
            self.config = MoncicConfig.load()
        else:
            self.config = config

        # Class used to instantiate systems
        self.system_class: Type[System]
        if system_class is None:
            self.system_class = System
        else:
            self.system_class = system_class

        # Actual image directory (might be None if not mounted)
        self.imagedir: Optional[str] = None

        # ExitStack tracking context managers associated to Moncic's usage as
        # context manager
        self.exit_stack = contextlib.ExitStack()

    def __enter__(self):
        if os.path.isdir(self.config.imagedir):
            self.imagedir = self.config.imagedir
        else:
            imagefile = os.path.join(self.config.imagedir)
            self.imagedir = self.exit_stack.enter_context(tempfile.TemporaryDirectory())
            with self.privs.root():
                self.exit_stack.enter_context(pause_automounting(imagefile))
                subprocess.run(["mount", imagefile, self.imagedir], check=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not os.path.isdir(self.config.imagedir):
            with self.privs.root():
                subprocess.run(["umount", self.imagedir], check=True)
        with self.privs.root():
            self.exit_stack.close()
        self.imagedir = None

    def create_system(self, name_or_path: str) -> System:
        """
        Instantiate a System from its name or path
        """
        if os.path.isdir(name_or_path):
            return self.system_class.from_path(self, name_or_path)
        else:
            return self.system_class.from_path(self, os.path.join(self.imagedir, name_or_path))

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
        """
        Attempt deduplicating files that have the same name and size across OS
        images
        """
        from .btrfs import do_dedupe

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

        with self.privs.root():
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

    def maybe_trim_image_file(self):
        """
        Run fstrim on the image file if requested by config or if we can see
        that the image file is on a SSD
        """
        if os.path.isdir(self.config.imagedir):
            return

        do_trim = self.config.trim_image_file
        if do_trim is None:
            rot = is_on_rotational(self.config.imagedir)
            if rot or rot is None:
                return
        elif not do_trim:
            return

        log.info("%s: trimming unused storage", self.config.imagedir)
        with self.privs.root():
            subprocess.run(["fstrim", self.imagedir], check=True)
