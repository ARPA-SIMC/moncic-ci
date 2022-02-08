from __future__ import annotations
import contextlib
import logging
import os
import re
import shlex
import subprocess
import tempfile
from typing import Optional
import urllib.parse

from .cli import Command, Fail
from .system import System
from .runner import LocalRunner
from .build import Builder

log = logging.getLogger(__name__)


def sh(*cmd):
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise Fail(f"Command '{' '.join(shlex.quote(c) for c in cmd)}' exited with status {e.returncode}")


@contextlib.contextmanager
def checkout(repo: Optional[str] = None, branch: Optional[str] = None):
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
            runner = LocalRunner(cmd, cwd=workdir)
            runner.run()
            # Look for the directory that git created
            names = os.listdir(workdir)
            if len(names) != 1:
                raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")
            yield os.path.join(workdir, names[0])


class CI(Command):
    """
    clone a git repository and launch a container instance in the
    requested OS chroot executing a build in the cloned source tree
    according to .travis-build.sh script (or <buildscript> if set)
    """
    NAME = "ci"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("-I", "--imagedir", action="store", default="./images",
                            help="path to the directory that contains container images. Default: ./images")
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
        root = self.args.system
        if not os.path.isdir(root):
            root = os.path.join(self.args.imagedir, root)

        system = System(root)
        with checkout(self.args.repo, branch=self.args.branch) as srcdir:
            run = system.create_ephemeral_run()
            run.workdir = srcdir
            if self.args.build_style:
                builder = Builder.create(self.args.build_style, run)
            else:
                builder = Builder.detect(run)
            with run:
                res = run.run_callable(builder.build)
            return res["returncode"]


class Shell(Command):
    """
    Run a shell in the given container
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("path", help="path to the chroot")

        parser.add_argument("--maintenance", action="store_true",
                            help="do not run ephemerally: changes will be preserved")

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
        system = System(self.args.path)
        if self.args.maintenance:
            run = system.create_maintenance_run()
        else:
            run = system.create_ephemeral_run()

        if self.args.bind:
            run.bind = self.args.bind
        if self.args.bind_ro:
            run.bind_ro = self.args.bind_ro

        with checkout(self.args.checkout) as workdir:
            run.workdir = workdir if workdir is not None else self.args.workdir
            run.shell(self.args.path)


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
            system = System(os.path.join(self.args.imagedir, name), name=name)
            with system.create_bootstrapper() as bootstrapper:
                if self.args.recreate and os.path.exists(system.root):
                    bootstrapper.remove()

                if not os.path.exists(system.root):
                    log.info("%s: bootstrapping subvolume", name)
                    try:
                        bootstrapper.bootstrap()
                    except Exception:
                        log.critical("%s: cannot create image", name, exc_info=True)
                        return 5

            with system.create_maintenance_run() as run:
                log.info("%s: updating subvolume", name)
                try:
                    run.update()
                except Exception:
                    log.critical("%s: cannot update image", name, exc_info=True)
                    return 6
