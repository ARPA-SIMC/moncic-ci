from __future__ import annotations

import urllib.parse

from moncic.distro import DistroFamily
from moncic.exceptions import Fail
from moncic.source import Source
from moncic.source.local import Git
from moncic.source.remote import URL

from .source import GitFixture

ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class TestURL(GitFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.git.add("testfile")
        cls.git.commit("Initial")

        # Debian branch
        cls.git.git("checkout", "-b", "branch1")
        cls.git.add("test-branch1")
        cls.git.commit()

        # New changes to upstream branch
        cls.git.git("checkout", "main")
        cls.git.add("test-main")
        cls.git.commit()

    def test_url(self):
        with self.git.serve() as url:
            with Source.create_local(source=url) as src:
                assert isinstance(src, Git)
                self.assertEqual(src.name, url)
                self.assertFalse(src.readonly)
                self.assertEqual(src.repo.active_branch.name, "main")

                remote = src.parent
                assert isinstance(remote, URL)
                self.assertEqual(remote.url, urllib.parse.urlparse(url))

                kwargs = remote.derive_kwargs()
                self.assertEqual(
                    kwargs,
                    {"parent": remote, "name": url, "url": remote.url},
                )

    def test_url_branch(self):
        with self.git.serve() as url:
            with Source.create_local(source=url, branch="branch1") as src:
                assert isinstance(src, Git)
                self.assertEqual(src.name, url)
                self.assertFalse(src.readonly)
                self.assertEqual(src.repo.active_branch.name, "branch1")

                assert isinstance(src.parent, URL)
                self.assertEqual(src.parent.url, urllib.parse.urlparse(url))
