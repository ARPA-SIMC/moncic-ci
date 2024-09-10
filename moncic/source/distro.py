from __future__ import annotations

import abc
from collections import defaultdict
from typing import TYPE_CHECKING, Any
from pathlib import Path
import shutil

from ..utils.run import run
from .local import LocalSource, File, Dir, Git
from .lint import Reporter

if TYPE_CHECKING:
    from ..distro import Distro


class DistroSource(LocalSource, abc.ABC):
    """
    Distribution-aware source
    """

    distro: Distro

    def __init__(self, *, distro: Distro, **kwargs) -> None:
        super().__init__(**kwargs)
        self.distro = distro

    def add_init_args_for_derivation(self, kwargs: dict[str, Any]) -> None:
        super().add_init_args_for_derivation(kwargs)
        kwargs["distro"] = self.distro

    def collect_build_artifacts(self, destdir: Path, artifact_dir: Path | None = None) -> None:
        """
        Gather build artifacts host system and copy them to the target directory.

        :param destdir: target directory where artifacts are copied
        :param artifact_dir: if provided, it is an extra possible source of artifacts
        """
        # Do nothing by default
        pass

    def guest_lint(self, reporter: Reporter) -> None:
        """
        Perform consistency checks on the source in the guest system.

        This can assume distro-specific tools to be available.

        This cannot assume access to the original sources.
        """
        # TODO: mark guest_only? or is it depending on an upper layer?
        # Check for version mismatches
        versions = self.lint_find_versions()

        by_version: dict[str, list[str]] = defaultdict(list)
        for name, version in versions.items():
            if name.endswith("-release"):
                by_version[version.split("-", 1)[0]].append(name)
            else:
                by_version[version].append(name)
        if len(by_version) > 1:
            descs = [f"{v} in {', '.join(names)}" for v, names in by_version.items()]
            reporter.warning(self, f"Versions mismatch: {'; '.join(descs)}")

    def lint_find_versions(self) -> dict[str, str]:
        versions = super().lint_find_versions()

        # TODO: see how to ensure that this is only run in a guest system,
        # without depending on @guest_only

        # Check setup.py by executing it with --version
        if (self.path / "setup.py").exists():
            if python3 := shutil.which("python3"):
                res = run([python3, "setup.py", "--version"])
                if res.returncode == 0:
                    lines = res.stdout.splitlines()
                    if lines:
                        versions["setup.py"] = lines[-1].strip().decode()

        return versions

    @classmethod
    @abc.abstractmethod
    def create_from_file(cls, parent: File, *, distro: Distro) -> "DistroSource":
        """Create a distro-specific source from a File."""

    @classmethod
    @abc.abstractmethod
    def create_from_dir(cls, parent: Dir, *, distro: Distro) -> "DistroSource":
        """Create a distro-specific source from a Dir directory."""

    @classmethod
    @abc.abstractmethod
    def create_from_git(cls, parent: Git, *, distro: Distro) -> "DistroSource":
        """Create a distro-specific source from a Git repo."""

    @classmethod
    def create_from_local(cls, parent: LocalSource, *, distro: Distro, style: str | None = None) -> "DistroSource":
        """Create a distro-specific source from a local source."""
        # TODO: redo with a match on python 3.10+
        if isinstance(parent, Git):
            return cls.create_from_git(parent, distro=distro)
        elif isinstance(parent, Dir):
            return cls.create_from_dir(parent, distro=distro)
        elif isinstance(parent, File):
            return cls.create_from_file(parent, distro=distro)
        else:
            raise NotImplementedError(f"Local source type {parent.__class__} not supported")
