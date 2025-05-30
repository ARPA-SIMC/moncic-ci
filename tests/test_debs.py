import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from moncic.utils.deb import DebCache


def make_deb(workdir: Path, name: str, size: int, atime: int) -> None:
    with (workdir / (name + ".deb")).open("wb") as fd:
        os.ftruncate(fd.fileno(), size)
        os.utime(fd.fileno(), times=(atime, time.time()))


class TestDebCache(unittest.TestCase):
    def test_share(self) -> None:
        with tempfile.TemporaryDirectory() as workdir_str:
            workdir = Path(workdir_str)
            make_deb(workdir, "a", 1000, 1)
            make_deb(workdir, "b", 2000, 2)
            with DebCache(workdir, 5000) as cache:
                with mock.patch("os.chown"):
                    with cache.apt_archives() as aptdir:
                        self.assertTrue(os.path.exists(os.path.join(aptdir, "a.deb")))
                        self.assertTrue(os.path.exists(os.path.join(aptdir, "b.deb")))
                        make_deb(aptdir, "c", 1000, 3)
                self.assertTrue(os.path.exists(os.path.join(workdir, "a.deb")))
                self.assertTrue(os.path.exists(os.path.join(workdir, "b.deb")))
                self.assertTrue(os.path.exists(os.path.join(workdir, "c.deb")))

    def test_cache_limit(self) -> None:
        with tempfile.TemporaryDirectory() as workdir_str:
            workdir = Path(workdir_str)
            make_deb(workdir, "a", 1000, 1)
            make_deb(workdir, "b", 2000, 2)
            with DebCache(workdir, 4000) as cache:
                with mock.patch("os.chown"):
                    with cache.apt_archives() as aptdir:
                        self.assertTrue(os.path.exists(os.path.join(aptdir, "a.deb")))
                        self.assertTrue(os.path.exists(os.path.join(aptdir, "b.deb")))
                        make_deb(aptdir, "c", 1500, 3)
                self.assertTrue(os.path.exists(os.path.join(workdir, "a.deb")))
                self.assertTrue(os.path.exists(os.path.join(workdir, "b.deb")))
                self.assertTrue(os.path.exists(os.path.join(workdir, "c.deb")))
            self.assertFalse(os.path.exists(os.path.join(workdir, "a.deb")))
