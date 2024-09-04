from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import urllib.parse
from collections.abc import Generator
from typing import TYPE_CHECKING

import git

from ..container import BindConfig, ContainerConfig, RunConfig, UserConfig
from ..exceptions import Fail
from ..moncic import Moncic, MoncicConfig, expand_path
from ..source import InputSource, Source, get_source_class
from ..utils.privs import ProcessPrivs
from .base import Command
from .utils import SourceTypeAction

if TYPE_CHECKING:
    from ..distro import Distro
    from ..system import System

log = logging.getLogger(__name__)


MAIN_COMMANDS: list[type[Command]] = []


def main_command(cls):
    """
    Decorator used to register a Commnad class as a main Moncic-CI command
    """
    MAIN_COMMANDS.append(cls)
    return cls


@contextlib.contextmanager
def checkout(system: System, repo: str | None = None, branch: str | None = None):
    if repo is None:
        yield None
        return

    with system.images.session.moncic.privs.user():
        # If repo points to a local path, use its absolute path
        parsed = urllib.parse.urlparse(repo)
        if parsed.scheme not in ("", "file"):
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

    # Set to False if the command does not need root
    NEEDS_ROOT: bool = True

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        if "imagedir" not in parser.shared_args:
            parser.add_argument(
                "-I",
                "--imagedir",
                action="store",
                shared=True,
                help="path to the directory that contains container images."
                " Default: from configuration file, or /var/lib/machines",
            )
            parser.add_argument(
                "-C",
                "--config",
                action="store",
                shared=True,
                help="path to the Moncic-CI config file to use. By default,"
                " look in a number of well-known locations, see"
                " https://github.com/ARPA-SIMC/moncic-ci/blob/main/doc/moncic-ci-config.md",
            )
            parser.add_argument(
                "--extra-packages-dir",
                action="store",
                shared=True,
                help="directory where extra packages, if present, are added to package sources" " in containers",
            )
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

        if self.NEEDS_ROOT:
            # Do the rest as root
            self.moncic.privs.regain()

    def setup_moncic_config(self, config: MoncicConfig):
        """
        Customize configuration before a Moncic object is instantiated
        """
        if imagedir := expand_path(self.args.imagedir):
            config.imagedir = imagedir

        if self.args.extra_packages_dir:
            config.extra_packages_dir = expand_path(self.args.extra_packages_dir)


class ImageActionCommand(MoncicCommand):
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("system", help="name or path of the system to use")

        parser.add_argument(
            "--maintenance", action="store_true", help="run in maintenance mode: changes will be preserved"
        )

        git_workdir = parser.add_mutually_exclusive_group(required=False)
        git_workdir.add_argument(
            "-w", "--workdir", help="bind mount (writable) the given directory as working directory"
        )
        git_workdir.add_argument(
            "-W", "--workdir-volatile", help="bind mount (volatile) the given directory as working directory"
        )
        git_workdir.add_argument(
            "--clone", metavar="repository", help="checkout the given repository (local or remote) in the chroot"
        )

        parser.add_argument(
            "--bind",
            action="append",
            help="option passed to systemd-nspawn as is (see man systemd-nspawn)" " can be given multiple times",
        )
        parser.add_argument(
            "--bind-ro",
            action="append",
            help="option passed to systemd-nspawn as is (see man systemd-nspawn)" " can be given multiple times",
        )
        parser.add_argument(
            "--bind-volatile",
            action="append",
            help="same as --bind-ro, but it adds a volatile overlay to make the directory writable"
            " in the container. Can be given multiple times",
        )

        parser.add_argument(
            "-u",
            "--user",
            action="store_true",
            help="create a shell as the current user before sudo" " (default is root, or the owner of workdir)",
        )
        parser.add_argument(
            "-r",
            "--root",
            action="store_true",
            help="create a shell as root (useful if using workdir and still wanting a root shell)",
        )

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

                workdir_bind_type = None
                with checkout(system, self.args.clone) as workdir:
                    if workdir is None:
                        if self.args.workdir:
                            workdir = self.args.workdir
                            workdir_bind_type = "rw"
                        elif self.args.workdir_volatile:
                            workdir = self.args.workdir_volatile
                            workdir_bind_type = "volatile"
                    elif self.args.clone and workdir_bind_type is None:
                        workdir_bind_type = "volatile"

                    config = ContainerConfig(ephemeral=not self.args.maintenance)

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


class SourceCommand(MoncicCommand):
    """
    Command that operates on sources
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--branch", action="store", help="branch to be used. Default: let 'git clone' choose")
        parser.add_argument(
            "-s",
            "--source-type",
            action=SourceTypeAction,
            help="name of the procedure used to run the CI. Use 'list' to list available options."
            " Default: autodetect",
        )
        return parser

    @contextlib.contextmanager
    def source(self, distro: Distro, source: str) -> Generator[Source, None, None]:
        """
        Instantiate a Source object
        """
        with self.moncic.privs.user():
            with InputSource.create(self.args.source) as input_source:
                if self.args.branch:
                    input_source = input_source.branch(self.args.branch)
                if self.args.source_type:
                    source_cls = get_source_class(self.args.source_type)
                    source = source_cls.create(distro, input_source)
                else:
                    source = input_source.detect_source(distro)
                self.moncic.privs.regain()
                yield source
