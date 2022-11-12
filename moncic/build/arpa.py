from __future__ import annotations

import glob
import itertools
import json
import logging
import os
import shutil
import subprocess
from typing import TYPE_CHECKING, List, Optional

from ..distro import DnfDistro, YumDistro
from ..runner import UserConfig
from ..utils import guest_only, host_only, run
from .analyze import Analyzer
from .base import Builder, link_or_copy

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
            raise RuntimeError(f"Unsupported distro: {system.distro.name}")

        self.specfile = self.locate_specfile(srcdir)

    @classmethod
    def create(cls, system: System, srcdir: str) -> Builder:
        return cls(system, srcdir)

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
    def setup_container_guest(self):
        super().setup_container_guest()

        # Reinstantiate the module logger
        global log
        log = logging.getLogger(__name__)

    @host_only
    def get_build_deps(self) -> List[str]:
        with self.container() as container:
            # Build run config
            run_config = container.config.run_config()

            res = container.run_callable(
                    self.get_build_deps_in_container,
                    run_config)
            res.check_returncode()

            with open(os.path.join(container.get_root(), "srv", "moncic-ci", "build", "result.json"), "rt") as fd:
                result = json.load(fd)

        return result["packages"]

    @guest_only
    def get_build_deps_in_container(self):
        res = subprocess.run(
                ["/usr/bin/rpmspec", "--parse", self.specfile], stdout=subprocess.PIPE, text=True, check=True)
        packages = []
        for line in res.stdout.splitlines():
            if line.startswith("BuildRequires: "):
                print(repr(line[15:]))
                packages.append(line[15:].strip())
            with open("/srv/moncic-ci/build/result.json", "wt") as out:
                json.dump({"packages": packages}, out)

    @guest_only
    def build_in_container(self, source_only: bool = False) -> Optional[int]:
        # This is executed as a process in the running system; stdout and
        # stderr are logged
        self.setup_container_guest()

        # Install build dependencies
        run(self.builddep + ["-q", "-y", self.specfile])

        pkgname = os.path.basename(self.specfile)[:-5]

        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
            os.makedirs(f"/root/rpmbuild/{name}")

        if self.specfile.startswith("fedora/SPECS/"):
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
            run(["spectool", "-g", "-R", "--define", f"srcarchivename {pkgname}", self.specfile])
            if source_only:
                build_arg = "-br"
            else:
                build_arg = "-ba"
            run(["rpmbuild", build_arg, "--define", f"srcarchivename {pkgname}", self.specfile])
        else:
            # Convenzione SIMC per i repo con solo rpm
            for f in glob.glob("*.patch"):
                shutil.copy(f, "/root/rpmbuild/SOURCES/")
            run(["spectool", "-g", "-R", self.specfile])
            run(["rpmbuild", "-ba", self.specfile])

        return None

    @host_only
    def collect_artifacts(self, container: Container, destdir: str):
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

        if build_log_name is None:
            build_log_name = os.path.basename(self.specfile)[:-5] + ".buildlog"

        if os.path.exists(logfile := os.path.join(container_root, "srv", "moncic-ci", "buildlog")):
            self.log_capture_end()
            link_or_copy(
                    logfile, destdir, user=user,
                    filename=build_log_name)
            log.info("Saving build log to %s/%s", destdir, build_log_name)
