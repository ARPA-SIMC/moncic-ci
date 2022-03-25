from __future__ import annotations
import argparse
import contextlib
import csv
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Optional, Sequence, Any, TextIO, NamedTuple, TYPE_CHECKING
import urllib.parse

try:
    from texttable import Texttable
except ModuleNotFoundError:
    Texttable = None

from .cli import Command, Fail
from .container import ContainerConfig, RunConfig, UserConfig
from .build import Builder
from .moncic import Moncic
from .distro import DistroFamily

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
    else:
        # If repo points to a local path, use its absolute path
        parsed = urllib.parse.urlparse(repo)
        if parsed.scheme in ('', 'file'):
            repo = os.path.abspath(parsed.path)

        with tempfile.TemporaryDirectory() as workdir:
            # Git checkout in a temporary directory
            cmd = ["git", "clone", repo]
            if branch is not None:
                cmd += ["--branch", branch]
            system.local_run(cmd, config=RunConfig(cwd=workdir))
            # Look for the directory that git created
            names = os.listdir(workdir)
            if len(names) != 1:
                raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")
            yield os.path.join(workdir, names[0])


class MoncicCommand(Command):
    """
    Base class for commands that need a Moncic state
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("-I", "--imagedir", action="store",
                            help="path to the directory that contains container images."
                                 " Default: from configuration file, or ./images")
        return parser

    def __init__(self, args):
        super().__init__(args)
        self.moncic = Moncic()
        if self.args.imagedir:
            self.moncic.set_imagedir(self.args.imagedir)


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
        parser.add_argument("-s", "--system", action="store",
                            help="name or path of the system used to build")
        parser.add_argument("-b", "--build-style", action="store", default="travis",
                            help="name of the procedure used to run the CI. Default: 'travis'")
        parser.add_argument("repo", nargs="?", default=".",
                            help="path or url of the repository to build. Default: the current directory")
        return parser

    def run(self):
        with self.moncic.images() as images:
            system = images.create_system(self.args.system)
            with checkout(system, self.args.repo, branch=self.args.branch) as srcdir:
                container = system.create_container(config=ContainerConfig(ephemeral=True, workdir=srcdir))
                if self.args.build_style:
                    builder = Builder.create(self.args.build_style, container)
                else:
                    builder = Builder.detect(container)
                with self.moncic.privs.root():
                    with container:
                        res = container.run_callable(builder.build)
                return res.returncode


class ImageActionCommand(MoncicCommand):
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("system", help="name or path of the system to use")

        parser.add_argument("--maintenance", action="store_true",
                            help="do not run ephemerally: changes will be preserved")

        git_workdir = parser.add_mutually_exclusive_group(required=False)
        git_workdir.add_argument(
                            "-w", "--workdir",
                            help="bind mount (writable) the given directory in /root")
        git_workdir.add_argument(
                            "--clone", metavar="repository",
                            help="checkout the given repository (local or remote) in the chroot")

        parser.add_argument("--bind", action="append",
                            help="option passed to systemd-nspawn as is (see man systemd-nspawn)"
                                 " can be given multiple times")
        parser.add_argument("--bind-ro", action="append",
                            help="option passed to systemd-nspawn as is (see man systemd-nspawn)"
                                 " can be given multiple times")

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
        with self.moncic.images() as images:
            system = images.create_system(self.args.system)
            if not system.is_bootstrapped():
                raise Fail(f"{system.name!r} has not been bootstrapped")

            with checkout(system, self.args.clone) as workdir:
                workdir = workdir if workdir is not None else self.args.workdir
                if workdir is not None:
                    workdir = os.path.abspath(workdir)

                config = ContainerConfig(
                        ephemeral=not self.args.maintenance,
                        workdir=workdir)
                if workdir is not None or self.args.user:
                    config.forward_user = True

                if self.args.bind:
                    config.bind = self.args.bind
                if self.args.bind_ro:
                    config.bind_ro = self.args.bind_ro

                with self.moncic.privs.root():
                    with system.create_container(config=config) as container:
                        yield container


class Shell(ImageActionCommand):
    """
    Run a shell in the given container
    """

    def run(self):
        shell_candidates = []
        if "SHELL" in os.environ:
            shell_candidates.append(os.environ["SHELL"])
            shell_candidates.append(os.path.basename(os.environ["SHELL"]))
        shell_candidates.extend(("bash", "sh"))

        def find_shell():
            """
            lookup for a valid shell in the container
            """
            for cand in shell_candidates:
                pathname = shutil.which(cand)
                if pathname is not None:
                    print(pathname)
                    return
            raise RuntimeError(f"No valid shell found. Tried: {', '.join(shell_candidates)}")

        run_config = self.get_run_config()
        run_config.check = False

        with self.container() as container:
            res = container.run_callable(find_shell)
            res = container.run([res.stdout.strip().decode(), "--login"], config=run_config)
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
        with self.moncic.images() as images:
            if not self.args.systems:
                systems = images.list_images()
            else:
                systems = self.args.systems

            systems = images.add_dependencies(systems)

            for name in systems:
                system = images.create_system(name)
                with self.moncic.privs.root():
                    if self.args.recreate and os.path.exists(system.path):
                        system.remove()

                    if not os.path.exists(system.path):
                        log.info("%s: bootstrapping subvolume", name)
                        try:
                            system.bootstrap()
                        except Exception:
                            log.critical("%s: cannot create image", name, exc_info=True)
                            return 5

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
        with self.moncic.images() as images:
            if not self.args.systems:
                systems = images.list_images()
            else:
                systems = self.args.systems

            count_ok = 0
            count_failed = 0

            for name in systems:
                system = images.create_system(name)

                if not os.path.exists(system.path):
                    continue

                log.info("%s: updating subvolume", name)
                with self.moncic.privs.root():
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
        parser.add_argument("--recreate", action="store_true",
                            help="delete the images and recreate them from scratch")
        parser.add_argument("systems", nargs="+",
                            help="names or paths of systems to bootstrap. Default: all .yaml files and existing images")
        return parser

    def run(self):
        with self.moncic.images() as images:
            for name in self.args.systems:
                system = images.create_system(name)
                if not os.path.exists(system.path):
                    continue
                with self.moncic.privs.root():
                    system.remove()


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
        if self.args.csv or Texttable is None:
            output = CSVOutput(sys.stdout)
        else:
            output = TableOutput(
                    sys.stdout,
                    TextColumn("Name"),
                    TextColumn("Distro"),
                    TextColumn("Boostrapped"),
                    TextColumn("Path"))

        with self.moncic.images() as images:
            for name in images.list_images():
                system = images.create_system(name)
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
        if self.args.csv or Texttable is None:
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
        with self.moncic.images() as images:
            images.deduplicate()
