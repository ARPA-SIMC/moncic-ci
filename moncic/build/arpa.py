from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .. import context
from ..runner import UserConfig
from ..utils.guest import guest_only, host_only
from ..utils.run import run
from .build import Build
from .utils import link_or_copy

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger(__name__)


@dataclass
class RPM(Build):
    """
    Build RPM packages
    """
    specfile: str | None = None

    def __post_init__(self):
        from ..distro.rpm import DnfDistro, YumDistro
        if isinstance(self.distro, YumDistro):
            self.builddep = ["yum-builddep"]
        elif isinstance(self.distro, DnfDistro):
            self.builddep = ["dnf", "builddep"]
        else:
            raise RuntimeError(f"Unsupported distro: {self.system.distro.name}")
        self.specfile = self.source.locate_specfile()
        self.name = os.path.basename(self.specfile)[:-5]

    # @host_only
    # def get_build_deps(self) -> list[str]:
    #     with self.container() as container:
    #         # Build run config
    #         run_config = container.config.run_config()

    #         return container.run_callable(
    #                 self.get_build_deps_in_container,
    #                 run_config).result()

    @guest_only
    def get_build_deps_in_container(self) -> list[str]:
        specfile = self.locate_specfile(".")
        res = subprocess.run(
                ["/usr/bin/rpmspec", "--parse", specfile], stdout=subprocess.PIPE, text=True, check=True)
        packages = []
        for line in res.stdout.splitlines():
            if line.startswith("BuildRequires: "):
                packages.append(line[15:].strip())
        return packages


@dataclass
class ARPA(RPM):
    """
    ARPA/SIMC builder, building RPM packages using the logic previously
    configured for travis
    """

    @guest_only
    def build(self) -> None:
        if self.specfile is None:
            raise RuntimeError("specfile location has not been detected")
        pkgname = os.path.basename(self.specfile)[:-5]

        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
            os.makedirs(f"/root/rpmbuild/{name}")

        # Install build dependencies
        run(self.builddep + ["-y", self.specfile])

        if self.specfile.startswith("fedora/SPECS/"):
            # Convenzione SIMC per i repo upstream
            if os.path.isdir("fedora/SOURCES"):
                for root, dirs, fnames in os.walk("fedora/SOURCES"):
                    for fn in fnames:
                        shutil.copy(os.path.join(root, fn), "/root/rpmbuild/SOURCES/")
            with open(f"/root/rpmbuild/SOURCES/{pkgname}.tar", "wb") as fd:
                with context.moncic.get().privs.user():
                    self.trace_run(
                        ["git", "archive", f"--prefix={pkgname}/", "--format=tar", "HEAD"],
                        stdout=fd)
            self.trace_run(["gzip", f"/root/rpmbuild/SOURCES/{pkgname}.tar"])
            self.trace_run(["spectool", "-g", "-R", "--define", f"srcarchivename {pkgname}", self.specfile])
            if self.source_only:
                build_arg = "-br"
            else:
                build_arg = "-ba"
            self.trace_run(["rpmbuild", build_arg, "--define", f"srcarchivename {pkgname}", self.specfile])
        else:
            # Convenzione SIMC per i repo con solo rpm
            for f in glob.glob("*.patch"):
                shutil.copy(f, "/root/rpmbuild/SOURCES/")
            self.trace_run(["spectool", "-g", "-R", self.specfile])
            self.trace_run(["rpmbuild", "-ba", self.specfile])

        self.success = True

    @host_only
    def collect_artifacts(self, container: Container, destdir: str):
        container_root = container.get_root()

        user = UserConfig.from_sudoer()
        patterns = (
            "RPMS/*/*.rpm",
            "SRPMS/*.rpm",
        )
        basedir = os.path.join(container_root, "root/rpmbuild")
        for pattern in patterns:
            for file in glob.glob(os.path.join(basedir, pattern)):
                filename = os.path.basename(file)
                log.info("Copying %s to %s", filename, destdir)
                link_or_copy(file, destdir, user=user)
                self.artifacts.append(filename)
