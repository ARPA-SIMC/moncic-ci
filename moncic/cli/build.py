from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
from typing import TYPE_CHECKING

from ..build import Builder, Analyzer
from ..exceptions import Fail
from .base import Command
from .moncic import MoncicCommand, checkout, main_command

if TYPE_CHECKING:
    from ..moncic import MoncicConfig

log = logging.getLogger(__name__)


@main_command
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
        parser.add_argument("-s", "--build-style", action="store",
                            help="name of the procedure used to run the CI. Default: autodetect")
        parser.add_argument("-a", "--artifacts", metavar="dir", action="store",
                            help="directory where build artifacts will be stored")
        parser.add_argument("--source-only", action="store_true",
                            help="only build source packages")
        parser.add_argument("--shell", action="store_true",
                            help="open a shell after the build")
        parser.add_argument("system", action="store",
                            help="name or path of the system used to build")
        parser.add_argument("repo", nargs="?", default=".",
                            help="path or url of the repository to build. Default: the current directory")
        for cb in Builder.extra_args_callbacks:
            cb(parser)
        return parser

    def setup_moncic_config(self, config: MoncicConfig):
        super().setup_moncic_config(config)
        if self.args.artifacts:
            config.build_artifacts_dir = os.path.abspath(self.args.artifacts)
            if not os.path.exists(config.build_artifacts_dir):
                raise Fail(f"Artifact directory {config.build_artifacts_dir!r} does not exist")

    def run(self):
        with self.moncic.session() as session:
            images = session.images
            with images.system(self.args.system) as system:
                with checkout(system, self.args.repo, branch=self.args.branch) as srcdir:
                    builder_args = {
                        "system": system,
                        "srcdir": srcdir,
                        "args": self.args,
                    }
                    if self.args.build_style:
                        builder = Builder.create_builder(self.args.build_style, **builder_args)
                    else:
                        builder = Builder.detect(**builder_args)
                    log.info("Build using builder %r", builder.__class__.__name__)

                    build_info = builder.build(shell=self.args.shell, source_only=self.args.source_only)
                    json.dump(dataclasses.asdict(build_info), sys.stdout, indent=1)
                    sys.stdout.write("\n")


@main_command
class Lint(Command):
    """
    Run consistency checks on a source directory using all available build
    styles
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("repo", nargs="?", default=".",
                            help="path or url of the repository to build. Default: the current directory")
        return parser

    def run(self):
        analyzer = Analyzer(self.args.repo)
        Builder.analyze(analyzer)


@main_command
class QuerySource(MoncicCommand):
    """
    Run consistency checks on a source directory using all available build
    styles
    """
    NAME = "query-source"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--branch", action="store",
                            help="branch to be used. Default: let 'git clone' choose")
        parser.add_argument("-s", "--build-style", action="store",
                            help="name of the procedure used to run the CI. Default: autodetect")
        parser.add_argument("system", action="store",
                            help="name or path of the system used to query the package")
        parser.add_argument("repo", nargs="?", default=".",
                            help="path or url of the repository to build. Default: the current directory")
        for cb in Builder.extra_args_callbacks:
            cb(parser)
        return parser

    def run(self):
        result = {}
        with self.moncic.session() as session:
            images = session.images
            with images.system(self.args.system) as system:
                result["distribution"] = system.distro.name
                with checkout(system, self.args.repo, branch=self.args.branch) as srcdir:
                    if self.args.build_style:
                        builder = Builder.create_builder(self.args.build_style, system, srcdir)
                    else:
                        builder = Builder.detect(system=system, srcdir=srcdir, args=self.args)

                    log.info("Query using builder %r", builder.__class__.__name__)

                    result["build-deps"] = builder.get_build_deps()

        json.dump(result, sys.stdout, indent=1)
        sys.stdout.write("\n")
