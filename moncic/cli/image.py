from __future__ import annotations

import argparse
import logging
import os
import shlex
import stat
from typing import TYPE_CHECKING, Any, Dict

import ruamel.yaml
import yaml

from ..exceptions import Fail
from ..utils import atomic_writer, edit_yaml
from .moncic import MoncicCommand, main_command

if TYPE_CHECKING:
    from ..session import Session

log = logging.getLogger(__name__)


@main_command
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
        group.add_argument("--install", "-i", nargs="+",
                           help="install the given packages in the image")
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
        elif self.args.install:
            self.do_install()
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

            log.info("%s: bootstrapping image", self.args.name)
            try:
                session.images.bootstrap_system(self.args.name)
            except Exception:
                log.error("%s: cannot create image", self.args.name, exc_info=True)

    def do_extends(self):
        self.create({"extends": self.args.extends})

    def do_distro(self):
        self.create({"distro": self.args.distro})

    def run_maintenance(self, session: Session):
        """
        Run system maintenance
        """
        with session.images.maintenance_system(self.args.name) as system:
            log.info("%s: updating image", self.args.name)
            try:
                system.update()
            except Exception:
                log.error("%s: cannot update image", self.args.name, exc_info=True)

    def do_setup(self):
        with self.moncic.session() as session:
            with self.moncic.privs.user():
                if path := session.images.find_config(self.args.name):
                    # Use ruamel.yaml to preserve comments
                    ryaml = ruamel.yaml.YAML(typ="rt")
                    with open(path, "rt") as fd:
                        data = ryaml.load(fd)
                        st = os.fstat(fd.fileno())
                        mode = stat.S_IMODE(st.st_mode)

                    if maintscript := data.get("maintscript"):
                        maintscript += "\n" + " ".join(shlex.quote(c) for c in self.args.setup)
                    else:
                        maintscript = " ".join(shlex.quote(c) for c in self.args.setup)
                    data["maintscript"] = ruamel.yaml.scalarstring.LiteralScalarString(maintscript)

                    with atomic_writer(path, "wt", chmod=mode) as out:
                        ryaml.dump(data, out)
                else:
                    raise Fail(f"{self.args.name}: configuration does not exist")

            self.run_maintenance(session)

    def do_install(self):
        changed = False

        with self.moncic.session() as session:
            with self.moncic.privs.user():
                if path := session.images.find_config(self.args.name):
                    # Use ruamel.yaml to preserve comments
                    ryaml = ruamel.yaml.YAML(typ="rt")
                    with open(path, "rt") as fd:
                        data = ryaml.load(fd)
                        st = os.fstat(fd.fileno())
                        mode = stat.S_IMODE(st.st_mode)

                    packages = data.get("packages")
                    if packages is None:
                        packages = []

                    # Add package names, avoiding duplicates
                    for name in self.args.install:
                        if name not in packages:
                            changed = True
                            packages.append(name)

                    data["packages"] = packages

                    if changed:
                        with atomic_writer(path, "wt", chmod=mode) as out:
                            ryaml.dump(data, out)
                else:
                    raise Fail(f"{self.args.name}: configuration does not exist")

            if changed:
                self.run_maintenance(session)

    def do_edit(self):
        changed = False

        with self.moncic.session() as session:
            if path := session.images.find_config(self.args.name):
                with self.moncic.privs.user():
                    with open(path, "rt") as fd:
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
