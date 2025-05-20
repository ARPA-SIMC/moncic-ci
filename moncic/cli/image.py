from __future__ import annotations

import argparse
import contextlib
import copy
import logging
import os
import shlex
import shutil
import stat
import sys
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

import ruamel.yaml
import yaml

from moncic.nspawn.image import NspawnImage

from ..exceptions import Fail
from ..operations import query as ops_query
from ..utils.edit import edit_yaml
from ..utils.fs import atomic_writer
from .moncic import MoncicCommand, SourceCommand, main_command

if TYPE_CHECKING:
    from ..session import Session

log = logging.getLogger(__name__)


class CreateCommand(MoncicCommand):
    def create(self, contents: dict[str, Any]):
        """
        Create a configuration with the given contents
        """
        with self.moncic.session() as session:
            with self.moncic.privs.user():
                image = session.images.image(self.args.name)
                if path := image.config_path:
                    raise Fail(f"{self.args.name}: configuration already exists in {path}")
                path = os.path.join(self.moncic.config.imageconfdirs[0], f"{self.args.name}.yaml")
                with atomic_writer(path, "wt", use_umask=True) as fd:
                    yaml.dump(
                        contents,
                        stream=fd,
                        default_flow_style=False,
                        allow_unicode=True,
                        explicit_start=True,
                        sort_keys=False,
                        Dumper=yaml.CDumper,
                    )

            log.info("%s: bootstrapping image", self.args.name)
            try:
                image = session.images.image(self.args.name)
                image.bootstrap()
            except Exception:
                log.error("%s: cannot create image", self.args.name, exc_info=True)


class Extends(CreateCommand):
    """
    create a new image, extending an existing one
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("image", help="parent image")
        return parser

    def run(self):
        self.create({"extends": self.args.image})


class Distro(CreateCommand):
    """
    create a new image, bootstrapping the given distribution
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("distro", help="distribution to bootstrap")
        return parser

    def run(self):
        self.create({"distro": self.args.distro})


class MaintCommand(MoncicCommand):
    def run_maintenance(self, session: Session):
        """
        Run system maintenance
        """
        image = session.images.image(self.args.name)
        assert isinstance(image, NspawnImage)
        with image.maintenance_system() as system:
            if not system.is_bootstrapped():
                return
            log.info("%s: updating image", self.args.name)
            try:
                system.update()
            except Exception:
                log.error("%s: cannot update image", self.args.name, exc_info=True)

    @contextlib.contextmanager
    def edit_config(self) -> Generator[dict[str, Any]]:
        """
        Edit the image configuration file as a parsed yaml structure.

        If the structure is changed, it writes it back to the configuration
        file, and then runs a maintenance update
        """
        changed = False
        with self.moncic.session() as session:
            with self.moncic.privs.user():
                image = session.images.image(self.args.name)
                if path := image.config_path:
                    # Use ruamel.yaml to preserve comments
                    ryaml = ruamel.yaml.YAML(typ="rt")
                    with open(path) as fd:
                        data = ryaml.load(fd)
                        st = os.fstat(fd.fileno())
                        mode = stat.S_IMODE(st.st_mode)

                    orig_data = copy.deepcopy(data)
                    yield data

                    if data != orig_data:
                        log.info("%s: updating configuration file", self.args.name)
                        changed = True
                        with atomic_writer(path, "wt", chmod=mode) as out:
                            ryaml.dump(data, out)
                else:
                    raise Fail(f"{self.args.name}: configuration does not exist")

        if changed:
            self.run_maintenance(session)


class Setup(MaintCommand):
    """
    run and record a maintenance command to setup the image
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument(
            "command", nargs=argparse.REMAINDER, help="run and record a maintenance command to setup the image"
        )
        return parser

    def run(self):
        with self.edit_config() as data:
            if maintscript := data.get("maintscript"):
                maintscript += "\n" + shlex.join(self.args.command)
            else:
                maintscript = shlex.join(self.args.command)
            data["maintscript"] = ruamel.yaml.scalarstring.LiteralScalarString(maintscript)


class Install(MaintCommand):
    """
    install the given packages in the image
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("packages", nargs="+", help="packages to install in the image")
        return parser

    def run(self):
        with self.edit_config() as data:
            packages = data.get("packages")
            if packages is None:
                packages = []

            # Add package names, avoiding duplicates
            for name in self.args.packages:
                if name not in packages:
                    packages.append(name)

            data["packages"] = packages


class BuildDep(SourceCommand):
    """
    install the build-dependencies of the given sources
    """

    NAME = "build-dep"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument(
            "source",
            nargs="?",
            default=".",
            help="path or url of the repository to build. Default: the current directory",
        )
        return parser

    def run(self):
        with self.moncic.session() as session:
            images = session.images
            image = images.image(self.args.system)
            with image.system() as system:
                with self.source(system.distro) as source:
                    operation = ops_query.BuildDeps(system, source)
                    packages = operation.host_main()

        log.info("Detected build-deps: %r", packages)

        with self.edit_config() as data:
            # Add package names, avoiding duplicates
            for name in packages:
                if name not in packages:
                    log.info("Adding package %r", name)
                    packages.append(name)

            data["packages"] = packages


class Edit(MaintCommand):
    """
    open an editor on the image configuration file
    """

    def run(self):
        changed = False

        with self.moncic.session() as session:
            image = session.images.image(self.args.name)
            if path := image.config_path:
                with self.moncic.privs.user():
                    with open(path) as fd:
                        buf = fd.read()
                        st = os.fstat(fd.fileno())
                        mode = stat.S_IMODE(st.st_mode)
                    edited = edit_yaml(buf, path)
                    if edited is not None:
                        changed = True
                        with atomic_writer(path, "wt", chmod=mode) as out:
                            out.write(edited)
            else:
                raise Fail(f"Configuration for {self.args.name} not found")

            if changed:
                self.run_maintenance(session)


class Cat(MoncicCommand):
    """
    show the image configuration
    """

    def run(self):
        with self.moncic.session() as session:
            image = session.images.image(self.args.name)
            if path := image.config_path:
                with self.moncic.privs.user():
                    print(f"# {path}")
                    with open(path) as fd:
                        shutil.copyfileobj(fd, sys.stdout)


class Describe(MoncicCommand):
    """
    show a description of the image
    """

    def run(self):
        ryaml = ruamel.yaml.YAML(typ="rt")
        with self.moncic.session() as session:
            image = session.images.image(self.args.name)
            info = image.describe_container()
            if maintscripts := info.get("maintscripts"):
                info["maintscripts"] = [
                    ruamel.yaml.scalarstring.LiteralScalarString(maintscript) for maintscript in maintscripts
                ]
            ryaml.dump(info, sys.stdout)


@main_command
class Image(MoncicCommand):
    """
    image creation and maintenance
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("name", help="name of the image")

        subparsers = parser.add_subparsers(help="sub-command help", dest="handler", required=True)
        Extends.make_subparser(subparsers)
        Distro.make_subparser(subparsers)
        Setup.make_subparser(subparsers)
        Install.make_subparser(subparsers)
        BuildDep.make_subparser(subparsers)
        Cat.make_subparser(subparsers)
        Describe.make_subparser(subparsers)
        Edit.make_subparser(subparsers)

        return parser

    def run(self):
        if self.args.install:
            self.do_install()
        else:
            raise NotImplementedError("cannot determine what to do")
