from __future__ import annotations
import dataclasses
import logging
import os
import subprocess
from typing import ContextManager, Optional, List

import yaml

from .session import Session
from .privs import ProcessPrivs

log = logging.getLogger(__name__)


@dataclasses.dataclass
class MoncicConfig:
    """
    Global Moncic-CI configuration
    """
    # Directory where images are stored
    imagedir: str = "/var/lib/machines"
    # Directories where image configuration can stored, if not found in
    # imagedir
    imageconfdirs: List[str] = dataclasses.field(default_factory=list)
    # Btrfs compression level to set on OS image subvolumes when they are
    # created. The value is the same as can be set by `btrfs property set
    # compression`. Default: nothing is set
    compression: Optional[str] = None
    # Automatically reexec with sudo if permissions are needed
    auto_sudo: bool = True
    # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
    tmpfs: bool = False
    # Directory where .deb files are cached between invocations
    debcachedir: Optional[str] = "~/.cache/moncic-ci/debs"
    # Directory where extra packages, if present, are added to package sources
    # in containers
    extra_packagages_dir: Optional[str] = None

    def __post_init__(self):
        # Allow to use ~ in config files
        self.imagedir = os.path.expanduser(self.imagedir)

        # Use ~ in imageconfdirs, and default to [$XDG_CONFIG_HOME/moncic-ci]
        if not self.imageconfdirs:
            self.imageconfdirs = [self.xdg_local_config_dir()]
        else:
            self.imageconfdirs = [os.path.expanduser(path) for path in self.imageconfdirs]

        self.debcachedir = os.path.expanduser(self.debcachedir)

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
            config: MoncicConfig,
            privs: Optional[ProcessPrivs] = None):
        self.privs: ProcessPrivs
        if privs is None:
            self.privs = ProcessPrivs()
        else:
            self.privs = privs

        self.config = config
        self.privs.auto_sudo = self.config.auto_sudo

        # Detect systemd's version
        res = subprocess.run(["systemctl", "--version"], check=True, capture_output=True, text=True)
        self.systemd_version = int(res.stdout.splitlines()[0].split()[1])

    def session(self) -> ContextManager[Session]:
        return Session(self)
