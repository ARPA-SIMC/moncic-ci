from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from moncic.context import privs
from ..runner import UserConfig
from ..utils.guest import guest_only, host_only
from ..utils.run import run
from .build import Build
from .utils import link_or_copy
from ..source.rpm import RPMSource

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger(__name__)


@dataclass
class RPM(Build):
    """
    Build RPM packages
    """

    def __post_init__(self):
        from ..distro.rpm import DnfDistro, YumDistro

        if isinstance(self.distro, YumDistro):
            self.builddep = ["yum-builddep"]
        elif isinstance(self.distro, DnfDistro):
            self.builddep = ["dnf", "builddep"]
        else:
            raise RuntimeError(f"Unsupported distro: {self.system.distro.name}")
        self.name = self.source.specfile_path.name.removesuffix(".spec")

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
        assert isinstance(self.source, RPMSource)
        specfile = self.source.path / self.source.specfile_path
        res = subprocess.run(["/usr/bin/rpmspec", "--parse", specfile], stdout=subprocess.PIPE, text=True, check=True)
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
        assert isinstance(self.source, RPMSource)

        rpmbuild_path = Path("/root/rpmbuild")

        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
            (rpmbuild_path / name).mkdir(parents=True, exist_ok=True)

        rpmbuild_sources = rpmbuild_path / "SOURCES"

        # Absolute path of specfile
        specfile = self.source.path / self.source.specfile_path

        # Install build dependencies
        run(self.builddep + ["-y", specfile.as_posix()])

        if self.source.specfile_path.is_relative_to("fedora/SPECS/"):
            # Convenzione SIMC per i repo upstream
            fedora_sources_dir = Path("fedora/SOURCES")
            if fedora_sources_dir.is_dir():
                for root, dirs, fnames in os.walk(fedora_sources_dir):
                    for fn in fnames:
                        shutil.copy(os.path.join(root, fn), rpmbuild_sources)
            source_tar = rpmbuild_sources / f"{self.name}.tar"
            with source_tar.open("wb") as fd:
                with privs.user():
                    self.trace_run(["git", "archive", f"--prefix={self.name}/", "--format=tar", "HEAD"], stdout=fd)
            self.trace_run(["gzip", source_tar.as_posix()])
            self.trace_run(["spectool", "-g", "-R", "--define", f"srcarchivename {self.name}", specfile.as_posix()])
            if self.source_only:
                build_arg = "-br"
            else:
                build_arg = "-ba"
            self.trace_run(["rpmbuild", build_arg, "--define", f"srcarchivename {self.name}", specfile.as_posix()])
        else:
            # Convenzione SIMC per i repo con solo rpm
            for f in glob.glob("*.patch"):
                shutil.copy(f, rpmbuild_sources)
            self.trace_run(["spectool", "-g", "-R", specfile.as_posix()])
            self.trace_run(["rpmbuild", "-ba", specfile.as_posix()])

        self.success = True

    @host_only
    def collect_artifacts(self, container: Container, destdir: Path):
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
