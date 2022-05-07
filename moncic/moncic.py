from __future__ import annotations
import dataclasses
import logging
import os
import subprocess
from typing import ContextManager, Optional, List

import yaml

from . import imagestorage
from .privs import ProcessPrivs

log = logging.getLogger(__name__)


@dataclasses.dataclass
class MoncicConfig:
    """
    Global Moncic-CI configuration
    """
    # Directory where images are stored
    imagedir: str = "/var/lib/machines"
    # Directory where image configuration is stored
    imageconfdir: List[str] = dataclasses.field(default_factory=list)
    # Btrfs compression level to set on OS image subvolumes when they are
    # created. The value is the same as can be set by `btrfs property set
    # compression`. Default: nothing is set
    compression: Optional[str] = None
    # If set to True, automatically run fstrim on the image file after regular
    # maintenance. If set to False, do not do that. By default, Moncic-CI will
    # run fstrim if it can detect that the image file is on a SSD
    trim_image_file: Optional[bool] = None
    # Automatically reexec with sudo if permissions are needed
    auto_sudo: bool = True
    # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
    tmpfs: bool = False

    def __post_init__(self):
        # Allow to use ~ in config files
        self.imagedir = os.path.expanduser(self.imagedir)

        # Use ~ in imageconfdir, and default to [$imagedir, $XDG_CONFIG_HOME/moncic-ci]
        if not self.imageconfdir:
            self.imageconfdir = [self.imagedir, os.path.join(self.xdg_local_config_dir())]
        else:
            self.imageconfdir = [os.path.expanduser(path) for path in self.imageconfdir]

    @classmethod
    def find_git_dir(cls) -> Optional[str]:
        try:
            res = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True)
        except FileNotFoundError:
            # Handle the rare case where git is missing
            return None
        if res.returncode != 0:
            return None
        path = res.stdout.strip()
        if path:
            return path
        return None

    @classmethod
    def xdg_local_config_dir(self) -> str:
        config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        return os.path.join(config_home, "moncic-ci")

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
        local_config = os.path.join(cls.xdg_local_config_dir(), "moncic-ci.yaml")
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

        return cls(**conf)


class Moncic:
    """
    General state of the Moncic-CI setup
    """
    def __init__(
            self,
            config: Optional[MoncicConfig] = None,
            privs: Optional[ProcessPrivs] = None):
        self.privs: ProcessPrivs
        if privs is None:
            self.privs = ProcessPrivs()
        else:
            self.privs = privs

        if config is None:
            self.config = MoncicConfig.load()
        else:
            self.config = config

        self.privs.auto_sudo = self.config.auto_sudo

        # Storage for OS images
        self.image_storage: imagestorage.ImageStorage
        if self.config.imagedir is None:
            self.image_storage = imagestorage.ImageStorage.create_default(self)
        else:
            self.image_storage = imagestorage.ImageStorage.create(self, self.config.imagedir)

        # Detect systemd's version
        res = subprocess.run(["systemd", "--version"], check=True, capture_output=True, text=True)
        self.systemd_version = int(res.stdout.splitlines()[0].split()[1])

    def images(self) -> ContextManager[imagestorage.Images]:
        return self.image_storage.images()

    def set_imagedir(self, imagedir: str):
        """
        Set the image directory, overriding the one from config
        """
        self.image_storage = imagestorage.ImageStorage.create(self, imagedir)
