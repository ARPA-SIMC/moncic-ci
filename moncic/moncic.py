from __future__ import annotations
import dataclasses
import graphlib
import os
import subprocess
from typing import List, Optional, Type, TYPE_CHECKING

import yaml

from .privs import ProcessPrivs

if TYPE_CHECKING:
    from .system import System


@dataclasses.dataclass
class MoncicConfig:
    """
    Global Moncic-CI configuration
    """
    # Directory where images are stored
    imagedir: str = os.path.abspath("./images")

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

    def create_system(self, name_or_path: str) -> System:
        """
        Instantiate a System from its name or path
        """
        if os.path.isdir(name_or_path):
            return self.system_class.from_path(self, name_or_path)
        else:
            return self.system_class.from_path(self, os.path.join(self.config.imagedir, name_or_path))

    def list_images(self) -> List[str]:
        """
        List the names of images found in image directories
        """
        res = set()
        for entry in os.scandir(self.config.imagedir):
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
            config = SystemConfig.load(os.path.join(self.config.imagedir, name))
            if config.extends is not None:
                res.add(config.name, config.extends)
            else:
                res.add(config.name)

        return list(res.static_order())
