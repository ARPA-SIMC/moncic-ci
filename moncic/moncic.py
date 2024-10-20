from __future__ import annotations

import dataclasses
import logging
import os
import subprocess

import yaml

from .session import MockSession, Session
from .utils.privs import ProcessPrivs

log = logging.getLogger(__name__)


def expand_path(path: str | None) -> str | None:
    """
    Process a path in the configuration, expanding ~ and making it absolute.

    If path is None or empty, return None
    """
    if not path:
        return None
    return os.path.abspath(os.path.expanduser(path))


@dataclasses.dataclass
class MoncicConfig:
    """
    Global Moncic-CI configuration
    """

    # Directory where images are stored
    imagedir: str = "/var/lib/machines"
    # Directories where image configuration can stored, if not found in
    # imagedir
    imageconfdirs: list[str] = dataclasses.field(default_factory=list)
    # Btrfs compression level to set on OS image subvolumes when they are
    # created. The value is the same as can be set by `btrfs property set
    # compression`. Default: nothing is set
    compression: str | None = None
    # Automatically reexec with sudo if permissions are needed
    auto_sudo: bool = True
    # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
    tmpfs: bool = False
    # Directory where .deb files are cached between invocations
    deb_cache_dir: str | None = "~/.cache/moncic-ci/debs"
    # Directory where extra packages, if present, are added to package sources
    # in containers
    extra_packages_dir: str | None = None
    # Directory where build artifacts will be stored
    build_artifacts_dir: str | None = None

    def __post_init__(self):
        # Allow to use ~ in config files
        self.imagedir = expand_path(self.imagedir)

        # Use ~ in imageconfdirs, and default to [$XDG_CONFIG_HOME/moncic-ci]
        if not self.imageconfdirs:
            self.imageconfdirs = [self.xdg_local_config_dir()]
        else:
            self.imageconfdirs = [expand_path(path) for path in self.imageconfdirs]

        self.deb_cache_dir = expand_path(self.deb_cache_dir)
        self.extra_packages_dir = expand_path(self.extra_packages_dir)
        self.build_artifacts_dir = expand_path(self.build_artifacts_dir)

    @classmethod
    def find_git_dir(cls) -> str | None:
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
    def find_config_file(cls) -> str | None:
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
    def load(cls, path: str | None = None):
        """
        Load the configuration from the given path, or from a list of default paths.
        """
        if path is None:
            path = cls.find_config_file()
        if path is None:
            # If no config file is loaded, keep the defaults
            return cls()

        try:
            with open(path) as fd:
                conf = yaml.load(fd, Loader=yaml.CLoader)
            log.info("Configuration loaded from %s", path)
        except FileNotFoundError:
            conf = None

        return cls(**conf)


class Moncic:
    """
    General state of the Moncic-CI setup
    """

    def __init__(self, config: MoncicConfig, privs: ProcessPrivs | None = None):
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

    def session(self) -> Session:
        """
        Create a new work session.

        Session is a context manager, so you can use this as `with moncic.session() as session:`
        """
        return Session(self)

    def mock_session(self) -> Session:
        """
        Create a new mock session for tests.

        Session is a context manager, so you can use this as `with moncic.mock_session() as session:`
        """
        return MockSession(self)
