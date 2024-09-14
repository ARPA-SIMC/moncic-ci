from __future__ import annotations

import abc
from collections import defaultdict
from typing import TYPE_CHECKING, Any, ClassVar
from pathlib import Path
import shutil

from ..utils.run import run
from .local import LocalSource, File, Dir, Git
from .lint import Reporter
from ..exceptions import Fail

if TYPE_CHECKING:
    from ..distro import Distro


# Registry of known builders
source_types: dict[str, type["DistroSource"]] = {}


class DistroSource(LocalSource, abc.ABC):
    """
    Distribution-aware source
    """

    style: ClassVar[str | None] = None
    distro: Distro

    def __init__(self, *, distro: Distro, **kwargs) -> None:
        super().__init__(**kwargs)
        self.distro = distro

    def __init_subclass__(cls, style: str | None = None, **kwargs) -> None:
        """Register subclasses."""
        super().__init_subclass__(**kwargs)
        if style is not None:
            cls.style = style
            source_types[style] = cls

    @classmethod
    def get_source_type(cls) -> str:
        """
        Return the user-facing name for this class
        """
        return cls.style or cls.__name__.lower()

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
    def prepare_from_file(cls, parent: File, *, distro: Distro) -> "DistroSource":
        """Create a distro-specific source from a File."""
        raise Fail(f"{cls.get_source_type()} is not applicable on a file")

    @classmethod
    def prepare_from_dir(cls, parent: Dir, *, distro: Distro) -> "DistroSource":
        """Create a distro-specific source from a Dir directory."""
        raise Fail(f"{cls.get_source_type()} is not applicable on a non-git directory")

    @classmethod
    def prepare_from_git(cls, parent: Git, *, distro: Distro) -> "DistroSource":
        """Create a distro-specific source from a Git repo."""
        raise Fail(f"{cls.get_source_type()} is not applicable on a git repository")

    @classmethod
    def create_from_local(cls, parent: LocalSource, *, distro: Distro, style: str | None = None) -> "DistroSource":
        """Create a distro-specific source from a local source."""
        source_cls: type["DistroSource"]
        if style is None:
            source_cls = cls._detect_class_for_distro(distro=distro)
            factory_method = "create_from_"
        else:
            source_cls = cls._detect_class_for_style(distro=distro, style=style)
            factory_method = "prepare_from_"

        # TODO: redo with a match on python 3.10+
        if isinstance(parent, Git):
            meth = getattr(source_cls, factory_method + "git")
        elif isinstance(parent, Dir):
            meth = getattr(source_cls, factory_method + "dir")
        elif isinstance(parent, File):
            meth = getattr(source_cls, factory_method + "file")
            return source_cls.create_from_file(parent, distro=distro)
        else:
            raise NotImplementedError(f"Local source type {parent.__class__} not supported")
        return meth(parent, distro=distro)

    @classmethod
    def _detect_class_for_distro(cls, *, distro: Distro) -> type["DistroSource"]:
        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro

        if isinstance(distro, DebianDistro):
            from .debian import DebianSource

            return DebianSource
        elif isinstance(distro, RpmDistro):
            from .rpm import RPMSource

            return RPMSource
        else:
            raise NotImplementedError(f"No suitable git builder found for distribution {distro!r}")

    @classmethod
    def _detect_class_for_style(cls, *, distro: Distro, style: str) -> type["DistroSource"]:
        style_cls = source_types.get(style, None)
        if style_cls is None:
            raise Fail(f"source type {style} not found. Use --source=type=list to get a list of availble ones")

        base_cls = cls._detect_class_for_distro(distro=distro)
        if not issubclass(style_cls, base_cls):
            raise Fail(f"source type {style} is not applicable for building on {distro}")

        return style_cls
