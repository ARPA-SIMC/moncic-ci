from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
from typing import TYPE_CHECKING

from ..build import Analyzer, Builder
from ..exceptions import Fail
from .base import Command
from .moncic import SourceCommand, main_command

if TYPE_CHECKING:
    from ..moncic import MoncicConfig

log = logging.getLogger(__name__)


@main_command
class CI(SourceCommand):
    """
    clone a git repository and launch a container instance in the
    requested OS chroot executing a build in the cloned source tree
    according to .travis-build.sh script (or <buildscript> if set)
    """
    NAME = "ci"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("-a", "--artifacts", metavar="dir", action="store",
                            help="directory where build artifacts will be stored")
        parser.add_argument("--source-only", action="store_true",
                            help="only build source packages")
        parser.add_argument("--shell", action="store_true",
                            help="open a shell after the build")
        parser.add_argument("--option", "-O", action="append",
                            help="key=value option for the build. See `-s list` for a list of"
                                 " available option for each build style")
        parser.add_argument("system", action="store",
                            help="name or path of the system used to build")
        parser.add_argument("source", nargs="?", default=".",
                            help="path or url of the repository or source package to build."
                                 " Default: the current directory")
        return parser

    def run(self):
        build_kwargs: dict[str, str] = {
            "artifacts_dir": self.moncic.config.build_artifacts_dir,
        }

        if self.args.artifacts:
            build_kwargs["artifacts_dir"] = os.path.abspath(self.args.artifacts)

        for option in self.args.option:
            if "=" not in option:
                raise Fail(f"option --option={option!r} must be key=value")
            k, v = option.split("=", 1)
            if not k:
                raise Fail(f"option --option={option!r} must have an non-empty key")
            build_kwargs[k] = v

        if (artifacts_dir := build_kwargs.get["artifacts_dir"]):
            os.makedirs(artifacts_dir, exist_ok=True)

        with self.moncic.session() as session:
            images = session.images
            with images.system(self.args.system) as system:
                builder = Builder(system)

                source = self.get_source(builder, self.args.source)
                log.info("Source type: %s", source.NAME)
                build_kwargs["source"] = source
                builder.setup_build(**build_kwargs)

                builder.run_build(shell=self.args.shell, source_only=self.args.source_only)
                json.dump(dataclasses.asdict(builder.build), sys.stdout, indent=1)
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
class QuerySource(SourceCommand):
    """
    Run consistency checks on a source directory using all available build
    styles
    """
    NAME = "query-source"

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("system", action="store",
                            help="name or path of the system used to query the package")
        parser.add_argument("source", nargs="?", default=".",
                            help="path or url of the repository to build. Default: the current directory")
        return parser

    def run(self):
        result = {}
        with self.moncic.session() as session:
            images = session.images
            with images.system(self.args.system) as system:
                builder = Builder(system)
                source = self.get_source(builder, self.args.source)
                result["distribution"] = system.distro.name
                log.info("Query using builder %r", builder.__class__.__name__)
                result["build-deps"] = builder.get_build_deps(source)

        json.dump(result, sys.stdout, indent=1)
        sys.stdout.write("\n")
