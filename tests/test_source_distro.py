from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from moncic.exceptions import Fail
from moncic.distro import DistroFamily
from moncic.source.source import CommandLog, SourceStack, Source
from moncic.source.distro import DistroSource, source_types
from moncic.source.local import File, Dir, Git
from moncic.distro.debian import DebianDistro
from moncic.distro.rpm import RpmDistro
from .source import GitFixture

if TYPE_CHECKING:
    from moncic.distro import Distro

ROCKY9 = cast(RpmDistro, DistroFamily.lookup_distro("rocky9"))
SID = cast(DebianDistro, DistroFamily.lookup_distro("sid"))


class MockSource(DistroSource, style="mock"):
    ...


class MockSource1(DistroSource):
    ...


class TestDistrSource(GitFixture):
    path_file: Path
    path_dir: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path_file = cls.workdir / "file.dsc"
        cls.path_file.touch()

        cls.path_dir = cls.workdir / "dir"
        cls.path_dir.mkdir()

        cls.git.add("testfile")
        cls.git.commit()

    def test_get_source_type(self) -> None:
        self.assertEqual(MockSource.get_source_type(), "mock")
        self.assertEqual(MockSource1.get_source_type(), "mocksource1")

    def test_registry(self) -> None:
        self.assertEqual(
            sorted(source_types.keys()),
            [
                "debian-dir",
                "debian-dsc",
                "debian-gbp-release",
                "debian-gbp-test",
                "debian-gbp-upstream",
                "mock",
                "rpm-arpa",
            ],
        )

    def test_create_default_file(self) -> None:
        parent = cast(File, Source.create_local(source=self.path_file))
        with self.assertRaisesRegex(Fail, "mock is not applicable on a file"):
            MockSource.prepare_from_file(parent, distro=SID)

    def test_create_default_dir(self) -> None:
        parent = cast(Dir, Source.create_local(source=self.path_file))
        with self.assertRaisesRegex(Fail, "mock is not applicable on a non-git directory"):
            MockSource.prepare_from_dir(parent, distro=SID)

    def test_create_default_git(self) -> None:
        parent = cast(Git, Source.create_local(source=self.git.root))
        with self.assertRaisesRegex(Fail, "mock is not applicable on a git repository"):
            MockSource.prepare_from_git(parent, distro=SID)
