from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, ClassVar
from pathlib import Path

from .local import LocalSource, File, Dir, Git
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

    def info_dict(self) -> dict[str, Any]:
        """Return JSON-able information about this source, without parent information."""
        res = super().info_dict()
        res["style"] = self.style
        res["distro"] = self.distro
        return res

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

    @classmethod
    @abc.abstractmethod
    def create_from_file(cls, parent: File, *, distro: Distro) -> "DistroSource":
        """
        Create a distro-specific source from a File.

        Autodetect the source style.
        """

    @classmethod
    @abc.abstractmethod
    def create_from_dir(cls, parent: Dir, *, distro: Distro) -> "DistroSource":
        """
        Create a distro-specific source from a Dir directory.

        Autodetect the source style.
        """

    @classmethod
    @abc.abstractmethod
    def create_from_git(cls, parent: Git, *, distro: Distro) -> "DistroSource":
        """
        Create a distro-specific source from a Git repo.

        Autodetect the source style.
        """

    @classmethod
    def prepare_from_file(cls, parent: File, *, distro: Distro) -> "DistroSource":
        """
        Create a distro-specific source from a File.

        This does not autodetect the source style, and is used to instantiate a
        well-defined one.
        """
        raise Fail(f"{cls.get_source_type()} is not applicable on a file")

    @classmethod
    def prepare_from_dir(cls, parent: Dir, *, distro: Distro) -> "DistroSource":
        """
        Create a distro-specific source from a Dir directory.

        This does not autodetect the source style, and is used to instantiate a
        well-defined one.
        """
        raise Fail(f"{cls.get_source_type()} is not applicable on a non-git directory")

    @classmethod
    def prepare_from_git(cls, parent: Git, *, distro: Distro) -> "DistroSource":
        """
        Create a distro-specific source from a Git repo.

        This does not autodetect the source style, and is used to instantiate a
        well-defined one.
        """
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
            raise Fail(f"source type {style} not found. Use --source-type=list to get a list of available ones")

        base_cls = cls._detect_class_for_distro(distro=distro)
        if not issubclass(style_cls, base_cls):
            raise Fail(f"source type {style} is not applicable for building on {distro}")

        return style_cls
