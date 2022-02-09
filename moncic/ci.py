from __future__ import annotations
import contextlib
import logging
import os
import shlex
import subprocess
import tempfile
from typing import Optional
import urllib.parse

from .cli import Command, Fail
from .runner import LocalRunner
from .build import Builder
from .moncic import Moncic

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


class MoncicCommand(Command):
    """
    Base class for commands that need a Moncic state
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("-I", "--imagedir", action="store", default="./images",
                            help="path to the directory that contains container images. Default: ./images")
        return parser

    def __init__(self, args):
        super().__init__(self, args)
        self.moncic = Moncic(self.args.imagedir)


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
        system = self.moncic.create_system(self.args.system)
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


class Shell(MoncicCommand):
    """
    Run a shell in the given container
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("system", help="name or path of the system to use")

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
        system = self.moncic.create_system(self.args.system)
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


class Bootstrap(MoncicCommand):
    """
    Create or update the whole set of OS images for the CI
    """
    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--recreate", action="store_true",
                            help="delete the images and recreate them from scratch")
        parser.add_argument("systems", nargs="+",
                            help="names or paths of systems to bootstrap")
        return parser

    def run(self):
        for name in self.args.distros:
            system = self.moncic.create_system(name)
            with system.create_bootstrapper() as bootstrapper:
                if self.args.recreate and os.path.exists(system.path):
                    bootstrapper.remove()

                if not os.path.exists(system.path):
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
