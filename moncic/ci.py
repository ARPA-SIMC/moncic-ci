from __future__ import annotations
import subprocess
import logging
import shutil
import shlex
import os
from .cli import Command, Fail
from .machine import Machine
from .distro import Distro

log = logging.getLogger(__name__)


def sh(*cmd):
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise Fail(f"Command '{' '.join(shlex.quote(c) for c in cmd)}' exited with status {e.returncode}")


class LaunchBuild(Command):
    """
    clone a git repository and launch a container instance in the
    requested OS chroot executing a build in the cloned source tree
    according to .travis-build.sh script (or <buildscript> if set)
    """
    NAME = "launch_build"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--shell", action="store_true",
                            help="run a shell instead of the build script")
        parser.add_argument("repo",
                            help="git url of the repository to clone")
        parser.add_argument("image",
                            help="image file or base directory to use as chroot")
        parser.add_argument("tag",
                            help="tag to be passed to the build script")
        parser.add_argument("branch",
                            help="branch to be used")
        parser.add_argument("buildscript", nargs="?", default=".travis-build.sh",
                            help="build script that must accept <tag> as argument")
        return parser

    def run(self):
        with Machine(self.args.image) as machine:
            log.info("Machine %s started", machine.machine_name)

            machine.run(["/usr/bin/git", "clone", self.args.repo, "--branch", self.args.branch])

            dirname = os.path.basename(self.args.repo)
            if dirname.endswith(".git"):
                dirname = dirname[:-4]
            # if not os.path.isdir(dirname):
            #     raise Fail(f"git clone of {self.args.repo!r} did not create {dirname!r}")

            # if [[ -n "$BUILDSCRIPT" ]]; then
            #     [[ -e "$BUILDSCRIPT" ]] || { echo "build script $BUILDSCRIPT does not exist"; exit 1; }
            #     buildscript=./.travis-build.sh
            # else
            #     buildscript=$(mktemp -p .)
            #     cp $BUILDSCRIPT $buildscript
            # fi

            machine.run([
                "/bin/sh", "-c",
                f"cd {shlex.quote(dirname)};"
                f"sh {shlex.quote(self.args.buildscript)} {shlex.quote(self.args.tag)}",
            ])


class Bootstrap(Command):
    """
    Bootstrap a minimal OS image in a btrfs snapshot
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        distro_list = ', '.join(repr(x) for x in Distro.list())
        parser.add_argument("path",
                            help="path to the btrfs subvolume to create")
        parser.add_argument("os_name",
                            help=f"name of the distro to install (one of {distro_list})")
        return parser

    def run(self):
        distro = Distro.create(self.args.os_name)
        distro.bootstrap_subvolume(self.args.path)


class Shell(Command):
    """
    Run a shell in the given container
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("path", help="path to the chroot")
        parser.add_argument("-x", "--ephemeral", action="store_true",
                            help="run the shell on an ephemeral machine")
        return parser

    def run(self):
        distro = Distro.from_ostree(self.args.path)
        distro.run_shell(self.args.path)


class Bootstrapper(Command):
    """
    Create or update the whole set of OS images for the CI
    """
    # TODO: configure these
    PROCDIR = "."
    IMGPATH = "images"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("conf", nargs="?", default=os.path.join(cls.PROCDIR, "distros.conf"),
                            help="path to the configuration file. Defaut: %(default)s")
        return parser

    def run(self):
        distros = []
        with open(self.args.conf, "rt") as fd:
            for line in fd:
                name, build_opzionale = line.split()
                distros.append({
                    "name": name,
                    "build_opzionale": build_opzionale == "true",
                })

        for info in distros:
            distro = Distro.create(info["name"])
            path = os.path.join(self.IMGPATH, info["name"])
            # distro.bootstrap_subvolume(self.args.path)

            # TODO: what is SIMCOP_MANUAL_EXEC?
            # TODO: # se esecuzione manuale, cancello immagini esistenti, se no mi fido di quelle che esistono
            # TODO: if $SIMCOP_MANUAL_EXEC; then
            # TODO:     rm -f ${imgpath}/${d}
            # TODO: fi
            if False:
                shutil.rmtree(path)

            if not os.path.exists(path):
                log.info("Creo immagine %s...", info["name"])
                try:
                    distro.bootstrap(path)
                except Exception:
                    log.critical("Errore nella creazione dell'immagine %s", info["name"], exc_info=True)
                    return 5
                log.info("...fatto")
            else:
                log.info("Aggiorno immagine %s...", info["name"])
                try:
                    distro.update(path)
                except Exception:
                    log.critical("Errore nell'update dell'immagine %s", info["name"], exc_info=True)
                    return 6
                log.info("...fatto")
