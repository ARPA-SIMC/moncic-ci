from __future__ import annotations

import glob
import itertools
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING, Dict, List, NamedTuple, Optional, Type

from .container import ContainerConfig
from .deb import apt_get_cmd
from .distro import DnfDistro, YumDistro
from .runner import UserConfig
from .utils import cd
from . import setns

if TYPE_CHECKING:
    from .container import Container, System

log = logging.getLogger(__name__)


def run(cmd, check=True, **kwargs):
    """
    subprocess.run wrapper that has check=True by default and logs the commands
    run
    """
    log.info("Run: %s", " ".join(shlex.quote(c) for c in cmd))
    return subprocess.run(cmd, check=check, **kwargs)


def link_or_copy(src: str, dstdir: str, user: Optional[UserConfig] = None):
    """
    Try to make a hardlink of src inside directory dstdir.

    If hardlinking is not possible, copy it
    """
    dest = os.path.join(dstdir, os.path.basename(src))
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)

    if user is not None:
        os.chown(dest, user.user_id, user.group_id)


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
    def create(cls, name: str, system: System, srcdir: str) -> "Builder":
        builder_cls = cls.builders[name]
        return builder_cls(system, srcdir)

    @classmethod
    def detect(cls, system: System, srcdir: str) -> "Builder":
        for builder_cls in reversed(cls.builders.values()):
            if builder_cls.builds(srcdir):
                return builder_cls(system, srcdir)
        raise RuntimeError(f"No suitable builder found for {srcdir!r}")

    @classmethod
    def builds(cls, srcdir: str) -> bool:
        """
        Check if the builder understands this source directory
        """
        raise NotImplementedError(f"{cls}.builds not implemented")

    def __init__(self, system: System, srcdir: str):
        """
        The constructor is run in the host system
        """
        self.system = system
        self.srcdir = srcdir
        self.user = UserConfig.from_sudoer()

    def build(self, shell: bool = False) -> int:
        """
        Run the build, store the artifacts in the given directory if requested,
        return the returncode of the build process
        """
        artifacts_dir = self.system.images.session.moncic.config.build_artifacts_dir
        container_config = ContainerConfig()
        container_config.configure_workdir(self.srcdir, bind_type="volatile", mountpoint="/srv/moncic-ci/source")
        container = self.system.create_container(config=container_config)
        with container:
            container_root = container.get_root()
            os.makedirs(os.path.join(container_root, "srv", "moncic-ci", "build"), exist_ok=True)
            build_config = container_config.run_config()
            build_config.user = UserConfig.root()
            try:
                res = container.run_callable(
                        self.build_in_container,
                        build_config,
                        kwargs={"workdir": "/srv/moncic-ci/build"})
                if artifacts_dir:
                    self.collect_artifacts(container, artifacts_dir)
            finally:
                if shell:
                    run_config = container_config.run_config()
                    run_config.interactive = True
                    run_config.check = False
                    run_config.user = UserConfig.root()
                    run_config.cwd = "/srv/moncic-ci/build"
                    container.run_shell(config=run_config)
        return res.returncode

    def build_in_container(self, workdir: str) -> Optional[int]:
        """
        Run the build in a child process.

        The function will be callsed inside the running system.

        The current directory will be set to the source directory.

        Standard output and standard error are logged.

        The return value will be used as the return code of the child process.
        """
        raise NotImplementedError(f"{self.__class__}.build not implemented")

    def collect_artifacts(self, container: Container, destdir: str):
        """
        Copy build artifacts to the given directory
        """
        # Do nothing by default
        pass


@Builder.register
class ARPA(Builder):
    def __init__(self, system: System, srcdir: str):
        super().__init__(system, srcdir)
        if isinstance(system.distro, YumDistro):
            self.builddep = ["yum-builddep"]
        elif isinstance(system.distro, DnfDistro):
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

    def build_in_container(self, workdir: str) -> Optional[int]:
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


class SourceInfo(NamedTuple):
    srcname: str
    version: str
    dsc_fname: str
    tar_fname: str
    changes_fname: str


def get_source_info() -> SourceInfo:
    """
    Return the file name of the .dsc file that would be created by the debian
    source package in the current directory
    """
    # Taken from debspawn
    pkg_srcname = None
    pkg_version = None
    res = run(["dpkg-parsechangelog"], capture_output=True, text=True)
    for line in res.stdout.splitlines():
        if line.startswith('Source: '):
            pkg_srcname = line[8:].strip()
        elif line.startswith('Version: '):
            pkg_version = line[9:].strip()

    if not pkg_srcname or not pkg_version:
        raise RuntimeError("Unable to determine source package name or source package version")

    res = run(["dpkg", "--print-architecture"], capture_output=True, text=True)
    arch = res.stdout.strip()

    pkg_version_dsc = pkg_version.split(":", 1)[1] if ":" in pkg_version else pkg_version
    dsc_fname = f"{pkg_srcname}_{pkg_version_dsc}.dsc"
    changes_fname = f"{pkg_srcname}_{pkg_version_dsc}_{arch}.changes"
    pkg_version_tar = pkg_version_dsc.split("-", 1)[0] if "-" in pkg_version_dsc else pkg_version_dsc
    tar_fname = f"{pkg_srcname}_{pkg_version_tar}.orig.tar.gz"

    return SourceInfo(pkg_srcname, pkg_version, dsc_fname, tar_fname, changes_fname)


def get_file_list(path: str) -> List[str]:
    """
    Read a .dsc or .changes file and return the list of files it references
    """
    res: List[str] = []
    is_changes = path.endswith(".changes")
    with open(path, "rt") as fd:
        in_files_section = False
        for line in fd:
            if in_files_section:
                if not line[0].isspace():
                    in_files_section = False
                else:
                    if is_changes:
                        checksum, size, section, priority, fname = line.strip().split(None, 4)
                    else:
                        checksum, size, fname = line.strip().split(None, 2)
                    res.append(fname)
            else:
                if line.startswith("Files:"):
                    in_files_section = True
    return res


@Builder.register
class Debian(Builder):
    @classmethod
    def builds(cls, srcdir: str) -> bool:
        if os.path.isdir(os.path.join(srcdir, "debian")):
            return True
        return False

    def build_source_plain(self, srcinfo: SourceInfo, workdir: str):
        with self.system.images.session.moncic.privs.user():
            with tempfile.TemporaryDirectory(dir=workdir) as clean_src:
                # Make a clean clone to avoid building from a dirty working
                # directory
                run(["git", "clone", ".", clean_src])

                with cd(clean_src):
                    # Build upstream tarball
                    # FIXME: this is a hack, that prevents building new debian versions
                    #        from the same upstream tarball
                    run(["git", "archive", f"--output=../{srcinfo.tar_fname}", "HEAD"])

                    # Uses --no-pre-clean to avoid requiring build-deps to be installed at
                    # this stage
                    run(["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"])

                    # No need to copy .dsc and its assets to the work
                    # directory, since we're building on a temporary subdir inside it

    def build_source_gbp(self, srcinfo: SourceInfo, workdir: str):
        with self.system.images.session.moncic.privs.user():
            with open(os.path.expanduser("~/.gbp.conf"), "wt") as fd:
                fd.write(f"[DEFAULT]\nexport-dir={workdir}\n")
                fd.flush()
            run(["gbp", "buildpackage", "--git-ignore-new",
                 "--git-upstream-tree=branch", "-d", "-S", "--no-sign",
                 "--no-pre-clean"])

    def build_in_container(self, workdir: str) -> Optional[int]:
        # TODO:
        # - inject dependency packages in a private apt repo if required
        #    - or export a local apt repo readonly

        os.chown("/srv/moncic-ci/source", self.user.user_id, self.user.group_id)
        os.chown(workdir, self.user.user_id, self.user.group_id)

        # Disable reindexing of manpages during installation of build-dependencies
        run(["debconf-set-selections"], input="man-db man-db/auto-update boolean false\n", text=True)

        # Build source package
        srcinfo = get_source_info()

        if os.path.exists("debian/gbp.conf"):
            self.build_source_gbp(srcinfo, workdir)
        else:
            self.build_source_plain(srcinfo, workdir)

        with cd(workdir):
            run(["dpkg-source", "-x", srcinfo.dsc_fname])

            # Find the newly created build directory
            with os.scandir(".") as it:
                for de in it:
                    if de.is_dir():
                        builddir = de.path
                        break
                else:
                    builddir = None

            with cd(builddir):
                # Install build dependencies
                env = dict(os.environ)
                env.update(DEBIAN_FRONTEND="noninteractive")
                run(apt_get_cmd("build-dep", "./"), env=env)

                # Build dependencies are installed, we don't need internet
                # anymore: Debian packages are required to build without
                # network access
                setns.unshare(setns.CLONE_NEWNET)

                # But we do need a working loopback
                run(["ip", "link", "set", "dev", "lo", "up"])

                # Build
                # Use unshare to disable networking
                run(["dpkg-buildpackage", "--no-sign"])

            # Collect artifacts
            artifacts_dir = "/srv/artifacts"
            if os.path.isdir(artifacts_dir):
                shutil.rmtree(artifacts_dir)
            os.makedirs(artifacts_dir)

            def collect(path: str):
                log.info("Found artifact %s", path)
                link_or_copy(path, artifacts_dir)

            collect(srcinfo.changes_fname)
            for fname in get_file_list(srcinfo.changes_fname):
                collect(fname)

    def collect_artifacts(self, container: Container, destdir: str):
        user = UserConfig.from_sudoer()
        with os.scandir(os.path.join(container.get_root(), "srv", "artifacts")) as it:
            for de in it:
                if de.is_file():
                    log.info("Copying %s to %s", de.name, destdir)
                    link_or_copy(de.path, destdir, user=user)
