from __future__ import annotations

import glob
import itertools
import logging
import os
import shutil
from typing import TYPE_CHECKING, Optional

from .distro import DnfDistro, YumDistro
from .runner import UserConfig
from .build import Builder, run, link_or_copy

if TYPE_CHECKING:
    from .container import Container, System

log = logging.getLogger(__name__)


@Builder.register
class RPM(Builder):
    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        travis_yml = os.path.join(srcdir, ".travis.yml")
        try:
            with open(travis_yml, "rt") as fd:
                if 'simc/stable' in fd.read():
                    return ARPA.create(system, srcdir)
        except FileNotFoundError:
            pass

        raise NotImplementedError("RPM source found, but simc/stable not found in .travis.yml for ARPA builds")


@Builder.register
class ARPA(RPM):
    """
    ARPA/SIMC builder, building RPM styles using the logic previously
    configured for travis
    """
    def __init__(self, system: System, srcdir: str):
        super().__init__(system, srcdir)
        if isinstance(system.distro, YumDistro):
            self.builddep = ["yum-builddep"]
        elif isinstance(system.distro, DnfDistro):
            self.builddep = ["dnf", "builddep"]
        else:
            raise RuntimeError(f"Unsupported distro: {run.system.distro.name}")

    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

    def setup_container_guest(self):
        super().setup_container_guest()
        # Reinstantiate the module logger
        global log
        log = logging.getLogger(__name__)

    def build_in_container(self) -> Optional[int]:
        # This is executed as a process in the running system; stdout and
        # stderr are logged
        spec_globs = ["fedora/SPECS/*.spec", "*.spec"]
        specs = list(itertools.chain.from_iterable(glob.glob(g) for g in spec_globs))

        if not specs:
            raise RuntimeError("Spec file not found")

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

    def collect_artifacts(self, container: Container, destdir: str):
        user = UserConfig.from_sudoer()
        patterns = (
            "RPMS/*/*.rpm",
            "SRPMS/*.rpm",
        )
        basedir = os.path.join(container.get_root(), "root/rpmbuild")
        for pattern in patterns:
            for file in glob.glob(os.path.join(basedir, pattern)):
                filename = os.path.basename(file)
                log.info("Copying %s to %s", filename, destdir)
                link_or_copy(file, destdir, user=user)
