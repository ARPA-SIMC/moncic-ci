from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import overload, Self

import yaml

from .session import MockSession, Session
from .utils.privs import ProcessPrivs

log = logging.getLogger(__name__)


@overload
def expand_path(path: Path) -> Path: ...
@overload
def expand_path(path: str) -> Path | None: ...


def expand_path(path: str | Path | None) -> Path | None:
    """
    Process a path in the configuration, expanding ~ and making it absolute.

    If path is None or empty, return None
    """
    if not path:
        return None
    return Path(path).expanduser().absolute()


class MoncicConfig:
    """
    Global Moncic-CI configuration
    """

    def __init__(self) -> None:
        # Directory where images are stored
        self.imagedir: Path = Path("/var/lib/machines")
        # Directories where image configuration can stored, if not found in
        # imagedir
        self.imageconfdirs: list[Path] = [self.xdg_local_config_dir()]
        # Btrfs compression level to set on OS image subvolumes when they are
        # created. The value is the same as can be set by `btrfs property set
        # compression`. Default: nothing is set
        self.compression: str | None = None
        # Automatically reexec with sudo if permissions are needed
        self.auto_sudo: bool = True
        # Use a tmpfs overlay for ephemeral containers instead of btrfs snapshots
        self.tmpfs: bool = False
        # Directory where .deb files are cached between invocations
        self.deb_cache_dir: Path | None = expand_path("~/.cache/moncic-ci/debs")
        # Directory where extra packages, if present, are added to package sources
        # in containers
        self.extra_packages_dir: Path | None = None
        # Directory where build artifacts will be stored
        self.build_artifacts_dir: Path | None = None

    @classmethod
    def find_git_dir(cls) -> Path | None:
        try:
            res = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True)
        except FileNotFoundError:
            # Handle the rare case where git is missing
            return None
        if res.returncode != 0:
            return None
        path = res.stdout.strip()
        if path:
            return Path(path)
        return None

    @classmethod
    def xdg_local_config_dir(self) -> Path:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")))
        return config_home / "moncic-ci"

    @classmethod
    def find_config_file(cls) -> Path | None:
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
            candidate = git_dir / "moncic-ci.yaml"
            if candidate.exists():
                return candidate

        # Try in the home directory, as ~/.config/moncic-ci/moncic-ci.yaml
        local_config = cls.xdg_local_config_dir() / "moncic-ci.yaml"
        if local_config.exists():
            return local_config

        # Try system-wide, as /etc/moncic-ci.yaml
        system_config = Path("/etc/moncic-ci.yaml")
        if system_config.exists():
            return system_config

        return None

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        """
        Load the configuration from the given path, or from a list of default paths.
        """
        if path is None:
            path = cls.find_config_file()
        if path is None:
            # If no config file is loaded, keep the defaults
            return cls()

        try:
            with path.open() as fd:
                conf = yaml.load(fd, Loader=yaml.CLoader)
            log.info("Configuration loaded from %s", path)
        except FileNotFoundError:
            return cls()

        res = cls()
        if imagedir := conf.pop("imagedir", None):
            res.imagedir = expand_path(imagedir)
        if imageconfdirs := conf.pop("imageconfdirs", None):
            res.imageconfdirs = [expand_path(d) for d in imageconfdirs]
        if compression := conf.pop("compression", None):
            res.compression = compression
        res.auto_sudo = conf.pop("auto_sudo", res.auto_sudo)
        res.tmpfs = conf.pop("tmpfs", res.tmpfs)
        if deb_cache_dir := conf.pop("deb_cache_dir", None):
            res.deb_cache_dir = expand_path(deb_cache_dir)
        if extra_packages_dir := conf.pop("extra_packages_dir", None):
            res.extra_packages_dir = expand_path(extra_packages_dir)
        if build_artifacts_dir := conf.pop("build_artifacts_dir", None):
            res.build_artifacts_dir = expand_path(build_artifacts_dir)
        return res


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
