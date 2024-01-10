from __future__ import annotations

import re
from collections import defaultdict
from functools import cached_property
from typing import Dict, List, Optional

import git


class Analyzer:
    def __init__(self):
        pass

    def error(self, message: str):
        print(message)

    def warning(self, message: str):
        print(message)

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
    def debian_packaging_branches(self) -> Dict[str, str]:
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

        res: Dict[str, str] = {}

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
    def version_from_debian_branches(self) -> Dict[str, str]:
        """
        Get the debian version from Debian branches

        Return a dict mapping version type to version
        """
        re_changelog = re.compile(r"\S+\s+\(([^)]+)\)")
        versions: Dict[str, str] = {}
        to_check = list(self.debian_packaging_branches.items())
        to_check.append((self.main_branch, self.main_branch))
        for name, branch_name in to_check:
            branch = self.repo.references[branch_name]
            if "debian" not in branch.commit.tree:
                continue
            changelog = branch.commit.tree["debian"]["changelog"]
            for line in changelog.data_stream.read().decode().splitlines():
                if mo := re_changelog.match(line):
                    versions[name] = mo.group(1)
                    break

        # Check for mismatches
        by_version: Dict[str, List[str]] = defaultdict(list)
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

        specs: List[git.objects.blob] = []
        for blob in specs_tree.blobs:
            if blob.name.endswith(".spec"):
                specs.append(blob)

        if not specs:
            self.warning(f"No specfile found in {self.main_branch}:fedora/SPECS")
            return None

        if len(specs) > 1:
            self.warning(
                f"Multiple specfiles found in {self.main_branch}:fedora/SPECS:" f" {', '.join(s.name for s in specs)}"
            )
            return None

        for line in specs[0].data_stream.read().decode().splitlines():
            if mo := re_version.match(line):
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
