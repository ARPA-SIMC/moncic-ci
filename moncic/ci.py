from __future__ import annotations
import argparse
import contextlib
import csv
import logging
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Optional, Sequence, Any, TextIO, NamedTuple, TYPE_CHECKING, Dict
import urllib.parse

try:
    from texttable import Texttable
    HAVE_TEXTTABLE = True
except ModuleNotFoundError:
    HAVE_TEXTTABLE = False

import git
import yaml

from .cli import Command, Fail
from .container import BindConfig, ContainerConfig, RunConfig, UserConfig
from .build import Builder
from . import build_arpa, build_debian  # noqa: import them so they are registered as builders
from .moncic import Moncic, MoncicConfig, expand_path
from .distro import DistroFamily
from .privs import ProcessPrivs
from .analyze import Analyzer
from .utils import atomic_writer, edit_yaml

if TYPE_CHECKING:
    from .system import System

log = logging.getLogger(__name__)


def sh(*cmd):
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise Fail(f"Command '{' '.join(shlex.quote(c) for c in cmd)}' exited with status {e.returncode}")


@contextlib.contextmanager
def checkout(system: System, repo: Optional[str] = None, branch: Optional[str] = None):
    if repo is None:
        yield None
        return

    with system.images.session.moncic.privs.user():
        # If repo points to a local path, use its absolute path
        parsed = urllib.parse.urlparse(repo)
        if parsed.scheme not in ('', 'file'):
            repo_abspath = repo
        else:
            repo_abspath = os.path.abspath(parsed.path)
            gitrepo = git.Repo(parsed.path)
            if gitrepo.active_branch == branch:
                system.images.session.moncic.privs.regain()
                yield repo_abspath
                return

        with tempfile.TemporaryDirectory() as workdir:
            # Git checkout in a temporary directory
            cmd = ["git", "-c", "advice.detachedHead=false", "clone", repo_abspath]
            if branch is not None:
                cmd += ["--branch", branch]
            system.local_run(cmd, config=RunConfig(cwd=workdir))
            # Look for the directory that git created
            names = os.listdir(workdir)
            if len(names) != 1:
                raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")

            repo_path = os.path.join(workdir, names[0])

            system.images.session.moncic.privs.regain()
            yield repo_path


class MoncicCommand(Command):
    """
    Base class for commands that need a Moncic state
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("-I", "--imagedir", action="store",
                            help="path to the directory that contains container images."
                                 " Default: from configuration file, or /var/lib/machines")
        parser.add_argument("-C", "--config", action="store",
                            help="path to the Moncic-CI config file to use. By default,"
                                 " look in a number of well-known locations, see"
                                 " https://github.com/ARPA-SIMC/moncic-ci/blob/main/doc/moncic-ci-config.md")
        parser.add_argument("--extra-packages-dir", action="store",
                            help="directory where extra packages, if present, are added to package sources"
                                 " in containers")
        return parser

    def __init__(self, args):
        super().__init__(args)

        privs = ProcessPrivs()

        # Load config
        with privs.user():
            if self.args.config:
                config = MoncicConfig.load(self.args.config)
            else:
                config = MoncicConfig.load()

            self.setup_moncic_config(config)

            # Instantiate Moncic
            self.moncic = Moncic(config=config, privs=privs)

        # Do the rest as root
        self.moncic.privs.regain()

    def setup_moncic_config(self, config: MoncicConfig):
        """
        Customize configuration before a Moncic object is instantiated
        """
        if (imagedir := expand_path(self.args.imagedir)):
            config.imagedir = imagedir

        if self.args.extra_packages_dir:
            config.extra_packages_dir = expand_path(self.args.extra_packages_dir)


class CI(MoncicCommand):
    """
    clone a git repository and launch a container instance in the
    requested OS chroot executing a build in the cloned source tree
    according to .travis-build.sh script (or <buildscript> if set)
    """
    NAME = "ci"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--branch", action="store",
                            help="branch to be used. Default: let 'git clone' choose")
        parser.add_argument("-s", "--build-style", action="store",
                            help="name of the procedure used to run the CI. Default: autodetect")
        parser.add_argument("-a", "--artifacts", metavar="dir", action="store",
                            help="directory where build artifacts will be stored")
        parser.add_argument("--source-only", action="store_true",
                            help="only build source packages")
        parser.add_argument("--shell", action="store_true",
                            help="open a shell after the build")
        parser.add_argument("system", action="store",
                            help="name or path of the system used to build")
        parser.add_argument("repo", nargs="?", default=".",
                            help="path or url of the repository to build. Default: the current directory")
        return parser

    def setup_moncic_config(self, config: MoncicConfig):
        super().setup_moncic_config(config)
        if self.args.artifacts:
            config.build_artifacts_dir = os.path.abspath(self.args.artifacts)

    def run(self):
        with self.moncic.session() as session:
            images = session.images
            with images.system(self.args.system) as system:
                with checkout(system, self.args.repo, branch=self.args.branch) as srcdir:
                    if self.args.build_style:
                        builder = Builder.create_builder(self.args.build_style, system, srcdir)
                    else:
                        builder = Builder.detect(system, srcdir)
                    log.info("Build using builder %r", builder.__class__.__name__)

                    return builder.build(shell=self.args.shell, source_only=self.args.source_only)


class Image(MoncicCommand):
    """
    image creation and maintenance
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("name",
                            help="name of the image")
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--extends", action="store", metavar="name",
                           help="create a new image, extending an existing one")
        group.add_argument("--distro", action="store", metavar="name",
                           help="create a new image, bootstrapping the given distribution")
        group.add_argument("--setup", "-s", action="store", nargs=argparse.REMAINDER,
                           help="run and record a maintenance command to setup the image")
        group.add_argument("--edit", action="store_true",
                           help="open an editor on the image configuration file")
        return parser

    def run(self):
        if self.args.extends:
            self.do_extends()
        elif self.args.distro:
            self.do_distro()
        elif self.args.setup:
            self.do_setup()
        elif self.args.edit:
            self.do_edit()
        else:
            raise NotImplementedError("cannot determine what to do")

    def create(self, contents: Dict[str, Any]):
        """
        Create a configuration with the given contents
        """
        with self.moncic.session() as session:
            with self.moncic.privs.user():
                if path := session.images.find_config(self.args.name):
                    raise Fail(f"{self.args.name}: configuration already exists in {path}")
                path = os.path.join(self.moncic.config.imageconfdirs[0], f"{self.args.name}.yaml")
                with atomic_writer(path, "wt", use_umask=True) as fd:
                    yaml.dump(contents, stream=fd, default_flow_style=False,
                              allow_unicode=True, explicit_start=True,
                              sort_keys=False, Dumper=yaml.CDumper)

            try:
                session.images.bootstrap_system(self.args.name)
            except Exception:
                log.error("%s: cannot create image", self.args.name, exc_info=True)

    def do_extends(self):
        self.create({"extends": self.args.extends})

    def do_distro(self):
        self.create({"distro": self.args.distro})

    def do_setup(self):
        raise NotImplementedError("setup")

    def do_edit(self):
        with self.moncic.session() as session:
            if path := session.images.find_config(self.args.name):
                with self.moncic.privs.user():
                    with open(path, "rt") as fd:
                        buf = fd.read()
                        st = os.fstat(fd.fileno())
                        mode = stat.S_IMODE(st.st_mode)
                    edited = edit_yaml(buf, path)
                    if edited is not None:
                        with atomic_writer(path, "wt", chmod=mode) as out:
                            out.write(edited)
            else:
                raise Fail(f"Configuration for {self.args.name} not found")


class ImageActionCommand(MoncicCommand):
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("system", help="name or path of the system to use")

        parser.add_argument("--maintenance", action="store_true",
                            help="run in maintenance mode: changes will be preserved")

        git_workdir = parser.add_mutually_exclusive_group(required=False)
        git_workdir.add_argument(
                            "-w", "--workdir",
                            help="bind mount (writable) the given directory as working directory")
        git_workdir.add_argument(
                            "-W", "--workdir-volatile",
                            help="bind mount (volatile) the given directory as working directory")
        git_workdir.add_argument(
                            "--clone", metavar="repository",
                            help="checkout the given repository (local or remote) in the chroot")

        parser.add_argument("--bind", action="append",
                            help="option passed to systemd-nspawn as is (see man systemd-nspawn)"
                                 " can be given multiple times")
        parser.add_argument("--bind-ro", action="append",
                            help="option passed to systemd-nspawn as is (see man systemd-nspawn)"
                                 " can be given multiple times")
        parser.add_argument("--bind-volatile", action="append",
                            help="same as --bind-ro, but it adds a volatile overlay to make the directory writable"
                                 " in the container. Can be given multiple times")

        parser.add_argument("-u", "--user", action="store_true",
                            help="create a shell as the current user before sudo"
                                 " (default is root, or the owner of workdir)")
        parser.add_argument("-r", "--root", action="store_true",
                            help="create a shell as root (useful if using workdir and still wanting a root shell)")

        return parser

    def get_run_config(self) -> RunConfig:
        run_config = RunConfig(interactive=True)
        if self.args.root:
            run_config.user = UserConfig.root()
        elif self.args.user:
            run_config.user = UserConfig.from_sudoer()
        return run_config

    @contextlib.contextmanager
    def container(self):
        with self.moncic.session() as session:
            images = session.images
            if self.args.maintenance:
                make_system = images.maintenance_system
            else:
                make_system = images.system

            with make_system(self.args.system) as system:
                if not system.is_bootstrapped():
                    raise Fail(f"{system.name!r} has not been bootstrapped")

                with checkout(system, self.args.clone) as workdir:
                    if workdir is None:
                        if self.args.workdir:
                            workdir = self.args.workdir
                            workdir_bind_type = "rw"
                        elif self.args.workdir_volatile:
                            workdir = self.args.workdir_volatile
                            workdir_bind_type = "volatile"

                    config = ContainerConfig(
                            ephemeral=not self.args.maintenance)

                    if workdir is not None:
                        config.configure_workdir(workdir, bind_type=workdir_bind_type)
                    elif self.args.user:
                        config.forward_user = UserConfig.from_sudoer()

                    if self.args.bind:
                        for entry in self.args.bind:
                            config.binds.append(BindConfig.from_nspawn(entry, bind_type="rw"))
                    if self.args.bind_ro:
                        for entry in self.args.bind_ro:
                            config.binds.append(BindConfig.from_nspawn(entry, bind_type="ro"))
                    if self.args.bind_volatile:
                        for entry in self.args.bind_volatile:
                            config.binds.append(BindConfig.from_nspawn(entry, bind_type="volatile"))

                    with system.create_container(config=config) as container:
                        yield container


class Shell(ImageActionCommand):
    """
    Run a shell in the given container
    """

    def run(self):
        run_config = self.get_run_config()
        run_config.check = False

        with self.container() as container:
            res = container.run_shell(config=run_config)
        return res.returncode


class Run(ImageActionCommand):
    """
    Run a shell in the given container
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("command", nargs=argparse.REMAINDER,
                            help="Command to run")
        return parser

    def run(self):
        run_config = self.get_run_config()
        run_config.use_path = True
        run_config.check = False

        with self.container() as container:
            res = container.run(self.args.command, config=run_config)
        return res.returncode


class Bootstrap(MoncicCommand):
    """
    Create or update the whole set of OS images for the CI
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--recreate", action="store_true",
                            help="delete the images and recreate them from scratch")
        parser.add_argument("systems", nargs="*",
                            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images")
        return parser

    def run(self):
        with self.moncic.session() as session:
            images = session.images

            if not self.args.systems:
                systems = images.list_images()
            else:
                systems = self.args.systems

            systems = images.add_dependencies(systems)

            for name in systems:
                if self.args.recreate:
                    images.remove_system(name)

                try:
                    images.bootstrap_system(name)
                except Exception:
                    log.critical("%s: cannot create image", name, exc_info=True)
                    return 5

                with images.maintenance_system(name) as system:
                    log.info("%s: updating subvolume", name)
                    try:
                        system.update()
                    except Exception:
                        log.critical("%s: cannot update image", name, exc_info=True)
                        return 6


class Update(MoncicCommand):
    """
    Update existing OS images
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("systems", nargs="*",
                            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images")
        return parser

    def run(self):
        with self.moncic.session() as session:
            images = session.images
            if not self.args.systems:
                systems = images.list_images()
            else:
                systems = self.args.systems

            count_ok = 0
            count_failed = 0

            for name in systems:
                with images.maintenance_system(name) as system:
                    if not os.path.exists(system.path):
                        continue

                    log.info("%s: updating subvolume", name)
                    try:
                        system.update()
                        count_ok += 1
                    except Exception:
                        log.critical("%s: cannot update image", name, exc_info=True)
                        count_failed += 1

            log.info("%d images successfully updated", count_ok)

            if count_failed:
                log.error("%d images failed to update", count_failed)
                return 6


class Remove(MoncicCommand):
    """
    Remove existing OS images
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("systems", nargs="+",
                            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images")
        parser.add_argument("--purge", "-P", action="store_true",
                            help="also remove the image configuration file")
        return parser

    def run(self):
        with self.moncic.session() as session:
            images = session.images
            for name in self.args.systems:
                images.remove_system(name)
                if self.args.purge:
                    images.remove_config(name)


class RowOutput:
    def add_row(self, row: Sequence[Any]):
        raise NotImplementedError(f"{self.__class__}.add_row() not implemented")

    def flush(self):
        pass


class CSVOutput(RowOutput):
    def __init__(self, out: TextIO):
        self.writer = csv.writer(out)

    def add_row(self, row: Sequence[Any]):
        self.writer.writerow(row)


class TextColumn(NamedTuple):
    title: str
    dtype: str = 't'
    align: str = 'l'


class TableOutput(RowOutput):
    def __init__(self, out: TextIO, *args: TextColumn):
        self.out = out
        self.table = Texttable(max_width=shutil.get_terminal_size()[0])
        self.table.set_deco(Texttable.HEADER)
        self.table.set_cols_dtype([a.dtype for a in args])
        self.table.set_cols_align([a.align for a in args])
        self.table.add_row([a.title for a in args])

    def add_row(self, row: Sequence[Any]):
        self.table.add_row(row)

    def flush(self):
        print(self.table.draw())


class Images(MoncicCommand):
    """
    List OS images
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--csv", action="store_true",
                            help="machine readable output in CSV format")
        return parser

    def run(self):
        if self.args.csv or not HAVE_TEXTTABLE:
            output = CSVOutput(sys.stdout)
        else:
            output = TableOutput(
                    sys.stdout,
                    TextColumn("Name"),
                    TextColumn("Distro"),
                    TextColumn("Boostrapped"),
                    TextColumn("Path"))

        with self.moncic.session() as session:
            images = session.images
            for name in images.list_images():
                with images.system(name) as system:
                    output.add_row((name, system.distro.name, "yes" if system.is_bootstrapped() else "no", system.path))
        output.flush()


class Distros(MoncicCommand):
    """
    List OS images
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--csv", action="store_true",
                            help="machine readable output in CSV format")
        return parser

    def run(self):
        if self.args.csv or not HAVE_TEXTTABLE:
            output = CSVOutput(sys.stdout)
        else:
            output = TableOutput(
                    sys.stdout,
                    TextColumn("Name"),
                    TextColumn("Shortcuts"))

        for family in sorted(DistroFamily.list(), key=lambda x: x.name):
            for info in family.list_distros():
                output.add_row((info.name, ", ".join(info.shortcuts)))
        output.flush()


class Dedup(MoncicCommand):
    """
    Deduplicate disk usage in image directories
    """
    def run(self):
        with self.moncic.session() as session:
            session.images.deduplicate()


class Analyze(Command):
    """
    Run consistency checks on a source directory using all available build
    styles
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("repo", nargs="?", default=".",
                            help="path or url of the repository to build. Default: the current directory")
        return parser

    def run(self):
        analyzer = Analyzer(self.args.repo)
        Builder.analyze(analyzer)
