from __future__ import annotations

import unittest

from moncic.distro import DistroFamily
from moncic.source import InputSource
from moncic.unittest import make_moncic

from .source import WorkdirFixtureMixin, GitFixtureMixin


ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class FindVersionsCommon(WorkdirFixtureMixin):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.workdir / "configure.ac").write_text("AC_INIT([test],[1.1],[enrico@enricozini.org]\n")
        (cls.workdir / "meson.build").write_text("project('test', 'cpp', version: '1.2')\n")
        (cls.workdir / "CMakeLists.txt").write_text('set(PACKAGE_VERSION "1.3")\n')
        (cls.workdir / "NEWS.md").write_text("# New in version 1.4\n")
        (cls.workdir / "setup.py").write_text("""
from setuptools import setup
setup(name='test', packages=['test'])
""")
        (cls.workdir / "test").mkdir()
        (cls.workdir / "test" / "__init__.py").write_text('__version__ = "1.5"')

    def _find_versions(self, dist) -> dict[str, str]:
        with InputSource.create(self.workdir) as isrc:
            src = isrc.detect_source(dist)
            linter_cls = src.get_linter_class()
            with (make_moncic() as moncic,
                    moncic.mock_session() as session,
                    session.images.system(dist.name) as system):
                linter = linter_cls(system, src)
                session.enqueue_process_result(stdout=b"1.5\n")
                return linter.find_versions()


class TestFindVersionsDebian(FindVersionsCommon, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.workdir / "debian").mkdir()
        (cls.workdir / "debian" / "changelog").write_text("test (1.6-1) UNRELEASED; urgency=low")

    def test_find_versions(self):
        self.assertEqual(self._find_versions(SID), {
            'autotools': '1.1',
            'meson': '1.2',
            'cmake': '1.3',
            'news': '1.4',
            'setup.py': '1.5',
            'debian-upstream': '1.6',
            'debian-release': '1.6-1',
        })

        # Consistency between tag and version/release in the spec file: a
        # specific tag must be consistent with the Version and Release fields
        # set in the spec file.

        # Consistency between tags and commit history: the changes between two
        # releases of the same upstream version (e.g. v${VERSION}-${RELEASE}
        # and v${VERSION}-${RELEASE-1} should affect only the package files
        # (spec, patch)
