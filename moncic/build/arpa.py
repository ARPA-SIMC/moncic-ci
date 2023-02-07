from __future__ import annotations

import glob
import itertools
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from ..runner import UserConfig
from ..utils.guest import guest_only, host_only
from ..utils.run import run
from .analyze import Analyzer
from .base import BuildInfo, Builder, link_or_copy

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger(__name__)


@dataclass
class RPMBuildInfo(BuildInfo):
    """
    BuildInfo class with gbp-buildpackage specific fields
    """
    specfile: Optional[str] = None


@Builder.register
class RPM(Builder):
    build_info_cls = RPMBuildInfo

    @classmethod
    def create(cls, *, srcdir: str, **kw) -> Builder:
        travis_yml = os.path.join(srcdir, ".travis.yml")
        try:
            with open(travis_yml, "rt") as fd:
                if 'simc/stable' in fd.read():
                    return ARPA.create(srcdir=srcdir, **kw)
        except FileNotFoundError:
            pass

        raise NotImplementedError("RPM source found, but simc/stable not found in .travis.yml for ARPA builds")

    def __init__(self, **kw):
        super().__init__(**kw)
        from ..distro.rpm import DnfDistro, YumDistro
        if isinstance(self.system.distro, YumDistro):
            self.builddep = ["yum-builddep"]
        elif isinstance(self.system.distro, DnfDistro):
            self.builddep = ["dnf", "builddep"]
        else:
            raise RuntimeError(f"Unsupported distro: {self.system.distro.name}")

    def locate_specfile(self, srcdir: str) -> str:
        """
        Locate the specfile in the given source directory.

        Return its path relative to srcdir
        """
        raise NotImplementedError(f"{self.__class__.__name__}.locate_specfile() is not implemented")

    @host_only
    def get_build_deps(self) -> List[str]:
        with self.container() as container:
            # Build run config
            run_config = container.config.run_config()

            return container.run_callable(
                    self.get_build_deps_in_container,
                    run_config).result()

    @guest_only
    def get_build_deps_in_container(self) -> List[str]:
        specfile = self.locate_specfile(".")
        res = subprocess.run(
                ["/usr/bin/rpmspec", "--parse", specfile], stdout=subprocess.PIPE, text=True, check=True)
        packages = []
        for line in res.stdout.splitlines():
            if line.startswith("BuildRequires: "):
                packages.append(line[15:].strip())
        return packages

    @guest_only
    def build_in_container(self, source_only: bool = False) -> RPMBuildInfo:
        build_info = super().build_in_container(source_only)
        build_info.specfile = self.locate_specfile(".")

        # Install build dependencies
        run(self.builddep + ["-q", "-y", build_info.specfile])

        return build_info

    @classmethod
    def analyze(cls, analyzer: Analyzer):
        # Check that spec version is in sync with upstream
        upstream_version = Analyzer.same_values(analyzer.version_from_sources)
        spec_version = analyzer.version_from_arpa_specfile
        if upstream_version and upstream_version != spec_version:
            analyzer.warning(f"Upstream version {upstream_version!r} is different than specfile {spec_version!r}")

        # TODO: check that upstream tag exists


@Builder.register
class ARPA(RPM):
    """
    ARPA/SIMC builder, building RPM packages using the logic previously
    configured for travis
    """
    @classmethod
    def create(cls, **kw) -> Builder:
        return cls(**kw)

    def locate_specfile(self, srcdir: str) -> str:
        """
        Locate the specfile in the given source directory.

        Return its path relative to srcdir
        """
        spec_globs = ["fedora/SPECS/*.spec", "*.spec"]
        specs = list(itertools.chain.from_iterable(glob.glob(os.path.join(srcdir, g)) for g in spec_globs))

        if not specs:
            raise RuntimeError("Spec file not found")

        if len(specs) > 1:
            raise RuntimeError(f"{len(specs)} .spec files found")

        return os.path.relpath(specs[0], start=srcdir)

    @guest_only
    def build_in_container(self, source_only: bool = False) -> RPMBuildInfo:
        build_info = super().build_in_container(source_only)

        pkgname = os.path.basename(build_info.specfile)[:-5]

        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
            os.makedirs(f"/root/rpmbuild/{name}")

        if build_info.specfile.startswith("fedora/SPECS/"):
            # Convenzione SIMC per i repo upstream
            if os.path.isdir("fedora/SOURCES"):
                for root, dirs, fnames in os.walk("fedora/SOURCES"):
                    for fn in fnames:
                        shutil.copy(os.path.join(root, fn), "/root/rpmbuild/SOURCES/")
            with open(f"/root/rpmbuild/SOURCES/{pkgname}.tar", "wb") as fd:
                with self.system.images.session.moncic.privs.user():
                    run(["git", "archive", f"--prefix={pkgname}/", "--format=tar", "HEAD"],
                        stdout=fd)
            run(["gzip", f"/root/rpmbuild/SOURCES/{pkgname}.tar"])
            run(["spectool", "-g", "-R", "--define", f"srcarchivename {pkgname}", build_info.specfile])
            if source_only:
                build_arg = "-br"
            else:
                build_arg = "-ba"
            run(["rpmbuild", build_arg, "--define", f"srcarchivename {pkgname}", build_info.specfile])
        else:
            # Convenzione SIMC per i repo con solo rpm
            for f in glob.glob("*.patch"):
                shutil.copy(f, "/root/rpmbuild/SOURCES/")
            run(["spectool", "-g", "-R", build_info.specfile])
            run(["rpmbuild", "-ba", build_info.specfile])

        build_info.success = True

        return build_info

    @host_only
    def collect_artifacts(self, container: Container, build_info: RPMBuildInfo, destdir: str):
        container_root = container.get_root()

        user = UserConfig.from_sudoer()
        patterns = (
            "RPMS/*/*.rpm",
            "SRPMS/*.rpm",
        )
        basedir = os.path.join(container_root, "root/rpmbuild")
        build_log_name: Optional[str] = None
        for pattern in patterns:
            for file in glob.glob(os.path.join(basedir, pattern)):
                filename = os.path.basename(file)
                if filename.endswith(".src.rpm"):
                    build_log_name = filename[:-8] + ".buildlog"
                log.info("Copying %s to %s", filename, destdir)
                link_or_copy(file, destdir, user=user)
                build_info.artifacts.append(filename)

        if build_log_name is None:
            build_log_name = os.path.basename(build_info.specfile)[:-5] + ".buildlog"

        if os.path.exists(logfile := os.path.join(container_root, "srv", "moncic-ci", "buildlog")):
            self.log_capture_end()
            link_or_copy(
                    logfile, destdir, user=user,
                    filename=build_log_name)
            log.info("Saving build log to %s/%s", destdir, build_log_name)
            build_info.artifacts.append(build_log_name)
