import argparse
import contextlib
import logging
import os
import tempfile
import urllib.parse
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, override, Any

import git

from moncic import context
from moncic.runner import RunConfig, UserConfig

from ..container import BindConfig, ContainerConfig, Container
from ..exceptions import Fail
from ..moncic import Moncic, MoncicConfig, expand_path
from ..source import Source
from ..source.distro import DistroSource
from ..source.local import LocalSource
from .base import Command
from .utils import SourceTypeAction

if TYPE_CHECKING:
    from moncic.nspawn.image import NspawnImage

    from ..distro import Distro

log = logging.getLogger(__name__)


MAIN_COMMANDS: list[type[Command]] = []


def main_command(cls: type[Command]) -> type[Command]:
    """
    Decorator used to register a Commnad class as a main Moncic-CI command
    """
    MAIN_COMMANDS.append(cls)
    return cls


@contextlib.contextmanager
def checkout(image: "NspawnImage", repo: str | None = None, branch: str | None = None) -> Generator[Path | None]:
    if repo is None:
        yield None
        return

    # If repo points to a local path, use its absolute path
    parsed = urllib.parse.urlparse(repo)
    if parsed.scheme not in ("", "file"):
        repo_abspath = repo
    else:
        repo_abspath = os.path.abspath(parsed.path)
        gitrepo = git.Repo(parsed.path)
        if gitrepo.active_branch == branch:
            yield Path(repo_abspath)
            return

    with tempfile.TemporaryDirectory() as workdir_str:
        workdir = Path(workdir_str)
        # Git checkout in a temporary directory
        cmd = ["git", "-c", "advice.detachedHead=false", "clone", repo_abspath]
        if branch is not None:
            cmd += ["--branch", branch]
        image.host_run(cmd, cwd=workdir)
        # Look for the directory that git created
        names = os.listdir(workdir)
        if len(names) != 1:
            raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")

        yield workdir / names[0]


class MoncicCommand(Command):
    """
    Base class for commands that need a Moncic state
    """

    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
        parser = super().make_subparser(subparsers)
        if "imagedir" not in parser.shared_args:
            parser.add_argument(
                "-I",
                "--imagedir",
                action="store",
                shared=True,
                type=Path,
                help="path to the directory that contains container images."
                " Default: from configuration file, or /var/lib/machines",
            )
            parser.add_argument(
                "-C",
                "--config",
                action="store",
                shared=True,
                type=Path,
                help="path to the Moncic-CI config file to use. By default,"
                " look in a number of well-known locations, see"
                " https://github.com/ARPA-SIMC/moncic-ci/blob/main/doc/moncic-ci-config.md",
            )
            parser.add_argument(
                "--extra-packages-dir",
                action="store",
                shared=True,
                type=Path,
                help="directory where extra packages, if present, are added to package sources" " in containers",
            )
        return parser

    def __init__(self, args: Any) -> None:
        super().__init__(args)

        # Drop privileges by default, regain them only when needed
        context.privs.drop()

        context.debug.set(self.args.debug)

        # Load config
        if self.args.config:
            config = MoncicConfig.load(self.args.config)
        else:
            config = MoncicConfig.load()

        self.setup_moncic_config(config)

        # Instantiate Moncic
        self.moncic = Moncic(config=config)

    def setup_moncic_config(self, config: MoncicConfig) -> None:
        """
        Customize configuration before a Moncic object is instantiated
        """
        if imagedir := expand_path(self.args.imagedir):
            config.imagedir = imagedir

        if self.args.extra_packages_dir:
            config.extra_packages_dir = expand_path(self.args.extra_packages_dir)


class ImageActionCommand(MoncicCommand):
    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
        parser = super().make_subparser(subparsers)
        parser.add_argument("system", help="name or path of the system to use")

        parser.add_argument(
            "--maintenance", action="store_true", help="run in maintenance mode: changes will be preserved"
        )

        git_workdir = parser.add_mutually_exclusive_group(required=False)
        git_workdir.add_argument(
            "-w", "--workdir", type=Path, help="bind mount (writable) the given directory as working directory"
        )
        git_workdir.add_argument(
            "-W", "--workdir-volatile", type=Path, help="bind mount (volatile) the given directory as working directory"
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
    def container(self) -> Generator[Container, None, None]:
        with self.moncic.session() as session:
            images = session.images
            image = images.image(self.args.system)
            if not image.bootstrapped:
                raise Fail(f"{image.name!r} has not been bootstrapped")

            workdir_bind_type = None
            with checkout(image, self.args.clone) as workdir:
                if workdir is None:
                    if self.args.workdir:
                        workdir = self.args.workdir
                        workdir_bind_type = "rw"
                    elif self.args.workdir_volatile:
                        workdir = self.args.workdir_volatile
                        workdir_bind_type = "volatile"
                elif self.args.clone and workdir_bind_type is None:
                    workdir_bind_type = "volatile"

            config = ContainerConfig()
            if workdir is not None:
                assert workdir_bind_type is not None
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

            if self.args.maintenance:
                with image.maintenance_container(config=config) as container:
                    yield container
            else:
                with image.container(config=config) as container:
                    yield container


class SourceCommand(MoncicCommand):
    """
    Command that operates on sources
    """

    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
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
    def local_source(self) -> Generator[LocalSource]:
        """
        Instantiate a local Source object
        """
        with Source.create_local(source=self.args.source, branch=self.args.branch) as source:
            yield source

    def distro_source(self, source: LocalSource, distro: "Distro") -> DistroSource:
        """
        Instantiate a DistroSource object from a local source
        """
        return DistroSource.create_from_local(source, distro=distro, style=self.args.source_type)

    @contextlib.contextmanager
    def source(self, distro: "Distro") -> Generator[DistroSource]:
        """
        Instantiate a DistroSource object in one go
        """
        with self.local_source() as local_source:
            log.debug("%s: local source type %s", local_source, local_source.__class__.__name__)
            yield self.distro_source(local_source, distro=distro)
