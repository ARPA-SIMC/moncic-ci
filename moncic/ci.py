from __future__ import annotations
import subprocess
import shlex
import os
from .cli import Command, Fail


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
        sh("git", "clone", self.args.repo, "--branch", self.args.branch)
        dirname = os.path.basename(self.args.repo)
        if dirname.endswith(".git"):
            dirname = dirname[:-4]

        if not os.path.isdir(dirname):
            raise Fail(f"git clone of {self.args.repo!r} did not create {dirname!r}")

        # if [[ -n "$BUILDSCRIPT" ]]; then
        #     [[ -e "$BUILDSCRIPT" ]] || { echo "build script $BUILDSCRIPT does not exist"; exit 1; }
        #     buildscript=./.travis-build.sh
        # else
        #     buildscript=$(mktemp -p .)
        #     cp $BUILDSCRIPT $buildscript
        # fi

        cmd = [
           f'--bind-ro={os.path.abspath(dirname)}:/root/src',
        ]
        if not self.args.shell:
            cmd += [
               "sh", "-c",
               f"cd /root/src/{shlex.quote(dirname)};"
               f"sh {shlex.quote(self.args.buildscript)} {shlex.quote(self.args.tag)}"
            ]

        if os.path.isfile(self.args.image):
            # filesystem image
            cmd = ["systemd-nspawn", "-i", self.args.image, "--ephemeral"] + cmd
            sh(*cmd)
        elif os.path.isdir(self.args.image):
            # local dir
            cmd = ["systemd-nspawn", "-D", self.args.image, "--volatile=overlay"] + cmd
        else:
            raise Fail(f"image {self.args.image!r} does not exist")
        # TODO: save the build result somehow
