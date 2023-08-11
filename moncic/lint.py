from __future__ import annotations

import contextlib
import re
from collections import defaultdict
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import git

from .container import ContainerConfig

if TYPE_CHECKING:
    from ..container import System
    from ..source.source import Source


class Linter(contextlib.ExitStack):
    """
    Scan sources for potential inconsistencies
    """
    def __init__(self, system: System, source: Source):
        super().__init__()
        # System used to analyze the sources
        self.system = system
        # Source to check
        self.source = source

    def error(self, message: str):
        print(message)

    def warning(self, message: str):
        print(message)

    @cached_property
    def source_path(self) -> Path:
        """
        Get the source path
        """
        return self.source.host_path

    def find_versions(self) -> dict[str, str]:
        """
        Get the program version from sources.

        Return a dict mapping version type to version strings
        """
        return self.source.find_versions(self.system)

    def check_local_remote_sync(self, name: str) -> str:
        """
        Check if branch {name} is in sync between local and remote.

        Return the name of the most up to date branch
        """
        if name not in self.repo.references:
            self.error(f"branch {name!r} does not exist locally")

        remote_name = "origin/" + name
        if remote_name not in self.repo.references:
            self.error(f"branch {remote_name!r} does not exist locally")

        local = self.repo.references[name]
        remote = self.repo.references[remote_name]
        if local.commit != remote.commit:
            if self.repo.is_ancestor(local.commit, remote.commit):
                self.warning(f"branch {remote_name} is ahead of local branch {name}")
                return remote_name
            elif self.repo.is_ancestor(remote.commit, local.commit):
                self.warning(f"branch {name} is ahead of remote branch {remote_name}")
                return name
            else:
                self.warning(f"branch {name} diverged from branch {remote_name}")
                return name
        else:
            return name

    @cached_property
    def main_branch(self) -> str:
        """
        Find the main branch name
        """
        for name in "main", "master":
            if name not in self.repo.branches:
                continue
            self.check_local_remote_sync(name)
            return name
        return "main"

    @cached_property
    def debian_packaging_branches(self) -> dict[str, str]:
        """
        List Debian/Ubuntu packaging branches found.

        Returns a dict mapping branch names to their most up to date version
        """
        tags = {x.name for x in self.repo.tags if x.name.split("/", 1)[0] in ("debian", "ubuntu")}

        local_branches = set()
        remote_branches = set()
        for x in self.repo.references:
            if x.name in tags:
                continue
            for distro in "debian", "ubuntu":
                if x.name.startswith(f"origin/{distro}/"):
                    remote_branches.add(x.name[7:])
                elif x.name.startswith(f"{distro}/"):
                    local_branches.add(x.name)

        res: dict[str, str] = {}

        for name in local_branches - remote_branches:
            self.error(f"branch {name!r} exists locally but not in origin")
            res[name] = name

        for name in remote_branches - local_branches:
            self.warning(f"branch {name!r} exists in origin but not locally")
            res[name] = "origin/" + name

        for name in local_branches & remote_branches:
            res[name] = self.check_local_remote_sync(name)

        return res

    @cached_property
    def upstream_version(self) -> Optional[str]:
        """
        Return the upstream version, if it can be univocally determined, else
        None
        """
        upstream_version = self.same_values(self.version_from_sources)
        if upstream_version is None:
            self.warning("Cannot univocally determine upstream version")
        return upstream_version

    @cached_property
    def version_from_debian_branches(self) -> dict[str, str]:
        """
        Get the debian version from Debian branches

        Return a dict mapping version type to version
        """
        re_changelog = re.compile(r"\S+\s+\(([^)]+)\)")
        versions: dict[str, str] = {}
        to_check = list(self.debian_packaging_branches.items())
        to_check.append((self.main_branch, self.main_branch))
        for name, branch_name in to_check:
            branch = self.repo.references[branch_name]
            if "debian" not in branch.commit.tree:
                continue
            changelog = branch.commit.tree["debian"]["changelog"]
            for line in changelog.data_stream.read().decode().splitlines():
                if (mo := re_changelog.match(line)):
                    versions[name] = mo.group(1)
                    break

        # Check for mismatches
        by_version: dict[str, list[str]] = defaultdict(list)
        for name, version in versions.items():
            by_version[version].append(name)
        if len(by_version) > 1:
            descs = [f"{v} in {', '.join(names)}" for v, names in by_version.items()]
            self.warning(f"Versions mismatch: {'; '.join(descs)}")

        return versions

    @cached_property
    def version_from_arpa_specfile(self) -> Optional[str]:
        """
        Get the version from ARPA's specfile
        """
        re_version = re.compile(r"\s*Version:\s+(\S+)")

        branch = self.repo.references[self.main_branch]
        try:
            specs_tree = branch.commit.tree["fedora"]["SPECS"]
        except KeyError:
            return None

        specs: list[git.objects.blob] = []
        for blob in specs_tree.blobs:
            if blob.name.endswith(".spec"):
                specs.append(blob)

        if not specs:
            self.warning(f"No specfile found in {self.main_branch}:fedora/SPECS")
            return None

        if len(specs) > 1:
            self.warning(f"Multiple specfiles found in {self.main_branch}:fedora/SPECS:"
                         f" {', '.join(s.name for s in specs)}")
            return None

        for line in specs[0].data_stream.read().decode().splitlines():
            if (mo := re_version.match(line)):
                return mo.group(1)

        return None

    @classmethod
    def same_values(cls, versions: dict[str, str]) -> Optional[str]:
        """
        If all the dict's entries have the same value, return that value.

        Else, return None
        """
        res = set(versions.values())
        if len(res) == 1:
            return next(iter(res))
        else:
            return None

    def lint(self):
        """
        lint-check the sources, using analyzer to output results
        """
        # Check for version mismatches
        versions = self.find_versions()

        by_version: dict[str, list[str]] = defaultdict(list)
        for name, version in versions.items():
            if name.endswith("-release"):
                by_version[version.split("-", 1)[0]].append(name)
            else:
                by_version[version].append(name)
        if len(by_version) > 1:
            descs = [f"{v} in {', '.join(names)}" for v, names in by_version.items()]
            self.warning(f"Versions mismatch: {'; '.join(descs)}")


class ARPALinter(Linter):
    def find_versions(self) -> dict[str, str]:
        versions = super().find_versions()

        spec_path = self.source.locate_specfile()

        # Run in container: rpmspec --parse file.spec
        if (self.source_path / spec_path).exists():
            cconfig = ContainerConfig()
            cconfig.configure_workdir(self.source_path, bind_type="ro")
            with self.system.create_container(config=cconfig) as container:
                res = container.run(["/usr/bin/rpmspec", "--parse", spec_path])
            if res.returncode == 0:
                version: Optional[str] = None
                release: Optional[str] = None
                for line in res.stdout.splitlines():
                    if line.startswith(b"Version:"):
                        if version is None:
                            version = line[8:].strip().decode()
                    if line.startswith(b"Release:"):
                        if release is None:
                            release = line[8:].strip().decode()

                if version is not None:
                    versions["spec-upstream"] = version
                    if release is not None:
                        versions["spec-release"] = version + "-" + release

        return versions

    def lint(self):
        super().lint()
        # # Check that spec version is in sync with upstream
        # upstream_version = Analyzer.same_values(analyzer.version_from_sources)
        # spec_version = analyzer.version_from_arpa_specfile
        # if upstream_version and upstream_version != spec_version:
        #     analyzer.warning(f"Upstream version {upstream_version!r} is different than specfile {spec_version!r}")

        # TODO: check that upstream tag exists


class DebianLinter(Linter):
    def find_versions(self) -> dict[str, str]:
        versions = super().find_versions()

        changelog = self.source_path / "debian" / "changelog"

        re_changelog = re.compile(r"\S+\s+\(([^)]+)\)")

        try:
            for line in changelog.read_text().splitlines():
                if (mo := re_changelog.match(line)):
                    debversion = mo.group(1)
                    if "-" in debversion:
                        upstream, release = debversion.split("-")
                    else:
                        upstream, release = debversion, None
                    versions["debian-upstream"] = upstream
                    if release is not None:
                        versions["debian-release"] = upstream + "-" + release
                    break
        except FileNotFoundError:
            pass

        return versions

    def lint(self):
        super().lint()
        # upstream_version = analyzer.upstream_version
        # debian_version = Analyzer.same_values(analyzer.version_from_debian_branches)

        # # Check that debian/changelog versions are in sync with upstream
        # if upstream_version is not None and debian_version is not None:
        #     if upstream_version not in debian_version:
        #         analyzer.warning(f"Debian version {debian_version!r} is out of sync"
        #                          f" with upstream version {upstream_version!r}")
        #     # if debian_version is None:
        #     #     analyzer.warning("Cannot univocally determine debian version")

        # # Check upstream merge status of the various debian branches
        # upstream_branch = analyzer.repo.references[analyzer.main_branch]
        # for name, branch_name in analyzer.debian_packaging_branches.items():
        #     debian_branch = analyzer.repo.references[branch_name]
        #     if not analyzer.repo.is_ancestor(upstream_branch, debian_branch):
        #         analyzer.warning(f"Upstream branch {analyzer.main_branch!r} is not merged in {name!r}")

        # TODO: check tags present for one distro but not for the other
        # TODO: check that upstream tag exists if debian/changelog is not UNRELEASED
