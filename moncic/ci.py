from __future__ import annotations
import subprocess
import logging
import shlex
import os
import re
from .cli import Command, Fail
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
        distro = Distro.from_ostree(self.args.image)
        with distro.machine(self.args.image) as machine:
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


class Shell(Command):
    """
    Run a shell in the given container
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("path", help="path to the chroot")

        # TODO: it should be ephemeral by default, use --persistent instead or --maintenance
        # parser.add_argument("-x", "--ephemeral", action="store_true",
        #                     help="run the shell on an ephemeral machine")

        git_workdir = parser.add_mutually_exclusive_group(required=False)
        git_workdir.add_argument(
                            "--workdir",
                            help="bind mount (writable) the given directory in /root")
        git_workdir.add_argument(
                            "--checkout", "--co",
                            help="checkout the given repository (local or remote) in the chroot")

        parser.add_argument("--bind", action="append",
                            help="option passed to systemd-nspawn as is (see man systemd-nspawn)"
                                 " can be given multiple times")
        parser.add_argument("--bind-ro", action="append",
                            help="option passed to systemd-nspawn as is (see man systemd-nspawn)"
                                 " can be given multiple times")
        return parser

    def run(self):
        distro = Distro.from_ostree(self.args.path)
        # FIXME: ephemeral is not passed, so it's true by default
        distro.run_shell(
                self.args.path, checkout=self.args.checkout,
                workdir=self.args.workdir,
                bind=self.args.bind, bind_ro=self.args.bind_ro)


class Bootstrap(Command):
    """
    Create or update the whole set of OS images for the CI
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("-I", "--imagedir", action="store", default="./images",
                            help="path to the directory that contains container images")
        parser.add_argument("--recreate", action="store_true",
                            help="delete the images and recreate them from scratch")
        parser.add_argument("distros", nargs="+",
                            help="distributions to bootstrap")
        return parser

    def remove_nested_subvolumes(self, path):
        """
        Run btrfs remove on all subvolumes nested inside the given path
        """
        # Fetch IDs of subvolumes to delete
        #
        # Use IDs rather than paths to avoid potential issues with exotic path
        # names
        re_btrfslist = re.compile(r"^ID (\d+) gen \d+ top level \d+ path (.+)$")
        res = subprocess.run(["btrfs", "subvolume", "list", "-o", path], check=True, text=True, capture_output=True)
        to_delete = []
        for line in res.stdout.splitlines():
            if mo := re_btrfslist.match(line):
                to_delete.append((mo.group(1), mo.group(2)))
            else:
                raise RuntimeError(f"Unparsable line in btrfs output: {line!r}")

        # Delete in reverse order
        for subvolid, subvolpath in to_delete[::-1]:
            log.info("removing btrfs subvolume %r", subvolpath)
            subprocess.run(["btrfs", "-q", "subvolume", "delete", "--subvolid", subvolid, path], check=True)

    def run(self):
        for name in self.args.distros:
            path = os.path.join(self.args.imagedir, name)
            if self.args.recreate and os.path.exists(path):
                self.remove_nested_subvolumes(path)
                log.info("removing btrfs subvolume %r", path)
                subprocess.run(["btrfs", "-q", "subvolume", "delete", path], check=True)

            distro = Distro.create(name)

            if not os.path.exists(path):
                log.info("%s: bootstrapping subvolume", name)
                try:
                    distro.bootstrap_subvolume(path)
                except Exception:
                    log.critical("%s: cannot create image", name, exc_info=True)
                    return 5

            log.info("%s: updating subvolume", name)
            try:
                distro.update(path)
            except Exception:
                log.critical("%s: cannot update image", name, exc_info=True)
                return 6
