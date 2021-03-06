from __future__ import annotations

import glob
import itertools
import logging
import os
import shlex
import shutil
import subprocess
from typing import TYPE_CHECKING, Dict, List, Optional, Type

from .distro import DnfDistro, YumDistro

if TYPE_CHECKING:
    from .container import Container

log = logging.getLogger(__name__)


class Builder:
    """
    Interface for classes providing the logic for CI builds
    """
    # Registry of known builders
    builders: Dict[str, Type[Builder]] = {}

    @classmethod
    def register(cls, builder_cls: Type["Builder"]) -> Type["Builder"]:
        name = getattr(builder_cls, "NAME", None)
        if name is None:
            name = builder_cls.__name__.lower()
        cls.builders[name] = builder_cls
        return builder_cls

    @classmethod
    def list(cls) -> List[str]:
        return list(cls.builders.keys())

    @classmethod
    def create(cls, name: str, run: Container) -> "Builder":
        builder_cls = cls.builders[name]
        return builder_cls(run)

    @classmethod
    def detect(cls, run: Container) -> "Builder":
        if run.config.workdir is None:
            raise ValueError("Running system has no workdir defined")
        for builder_cls in reversed(cls.builders.values()):
            if builder_cls.builds(run.config.workdir):
                return builder_cls(run)
        raise RuntimeError(f"No suitable builder found for {run.config.workdir!r}")

    @classmethod
    def builds(cls, srcdir: str) -> bool:
        """
        Check if the builder understands this source directory
        """
        raise NotImplementedError(f"{cls}.builds not implemented")

    def __init__(self, run: Container):
        """
        The constructor is run in the host system
        """
        self.run = run

    def build(self) -> Optional[int]:
        """
        Run the build in a child process.

        The function will be callsed inside the running system.

        The current directory will be set to the source directory.

        Standard output and standard error are logged.

        The return value will be used as the return code of the child process.
        """
        raise NotImplementedError(f"{self.__class__}.build not implemented")


@Builder.register
class ARPA(Builder):
    def __init__(self, run: Container):
        super().__init__(run)
        if isinstance(run.system.distro, YumDistro):
            self.builddep = ["yum-builddep"]
        elif isinstance(run.system.distro, DnfDistro):
            self.builddep = ["dnf", "builddep"]
        else:
            raise RuntimeError(f"Unsupported distro: {run.system.distro.name}")

    @classmethod
    def builds(cls, srcdir: str) -> bool:
        travis_yml = os.path.join(srcdir, ".travis.yml")
        try:
            with open(travis_yml, "rt") as fd:
                return 'simc/stable' in fd.read()
        except FileNotFoundError:
            return False

    def build(self) -> Optional[int]:
        def run(cmd, check=True, **kwargs):
            log.info("Run: %s", " ".join(shlex.quote(c) for c in cmd))
            return subprocess.run(cmd, check=check, **kwargs)

        # This is executed as a process in the running system; stdout and
        # stderr are logged
        spec_globs = ["fedora/SPECS/*.spec", "*.spec"]
        specs = list(itertools.chain.from_iterable(glob.glob(g) for g in spec_globs))

        if not specs:
            raise RuntimeError(f"Spec file not found")

        if len(specs) > 1:
            raise RuntimeError(f"{len(specs)} .spec files found")

        # Install build dependencies
        run(self.builddep + ["-q", "-y", specs[0]])

        pkgname = os.path.basename(specs[0])[:-5]

        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
            os.makedirs(f"/root/rpmbuild/{name}")

        if specs[0].startswith("fedora/SPECS/"):
            # Convenzione SIMC per i repo upstream
            if os.path.isdir("fedora/SOURCES"):
                for root, dirs, fnames in os.walk("fedora/SOURCES"):
                    for fn in fnames:
                        shutil.copy(os.path.join(root, fn), "/root/rpmbuild/SOURCES/")
            run(["git", "archive", f"--prefix={pkgname}/", "--format=tar", "HEAD",
                 "-o", f"/root/rpmbuild/SOURCES/{pkgname}.tar"])
            run(["gzip", f"/root/rpmbuild/SOURCES/{pkgname}.tar"])
            run(["spectool", "-g", "-R", "--define", f"srcarchivename {pkgname}", specs[0]])
            run(["rpmbuild", "-ba", "--define", f"srcarchivename {pkgname}", specs[0]])
        else:
            # Convenzione SIMC per i repo con solo rpm
            for f in glob.glob("*.patch"):
                shutil.copy(f, "/root/rpmbuild/SOURCES/")
            run(["spectool", "-g", "-R", specs[0]])
            run(["rpmbuild", "-ba", specs[0]])

        return None
