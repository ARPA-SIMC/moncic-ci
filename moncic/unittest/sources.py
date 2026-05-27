import shutil
import subprocess
import unittest
from pathlib import Path

top_srcdir = Path(__file__).parent.parent.parent.absolute()


class Package:
    """Test package from the integration test data."""

    def __init__(self, path: Path, name: str | None = None) -> None:
        self.name = name or path.name
        self.path = path

    def files(self) -> list[Path]:
        """Return the list of files (as relative paths) in this package."""
        res: list[Path] = []
        for dirpath, dirnames, filenames in self.path.walk():
            if dirpath == self.path and ".git" in dirnames:
                dirnames.remove(".git")
            for fn in filenames:
                res.append((dirpath / fn).relative_to(self.path))
        return res

    def as_git(self, path: Path) -> "Package":
        """Build a git repository of this package in the given path."""
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init"], cwd=path, capture_output=True, check=True
        )
        for file in self.files():
            src = self.path / file
            dest = path / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        subprocess.run(
            ["git", "add", "."], cwd=path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "initial commit"],
            cwd=path,
            capture_output=True,
            check=True,
        )
        return Package(path, self.name)

    def as_spec_in_root(self, path: Path) -> "Package":
        """Make a copy of this package with the specfile in the root."""
        path.mkdir(parents=True, exist_ok=True)
        for file in self.files():
            if file.is_relative_to(Path("fedora")):
                continue
            src = self.path / file
            dest = path / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        specfile = f"{self.name}.spec"
        shutil.copy2(self.path / "fedora" / "SPECS" / specfile, path / specfile)

        return Package(path, self.name)

    # If we need a source tarball:
    # with tarfile.open("hello_1.0.orig.tar.gz", "w:gz") as tar:
    #     for file in files:
    #         tar.add(file)


class SourcesTestCase(unittest.TestCase):
    """Test case with infrastructure to work with test source packages."""

    @classmethod
    def get_package(cls, name: str = "hello") -> Package:
        """Return a Package object from the integration test data."""
        packages_dir = top_srcdir / "tests"
        return Package(packages_dir / name)
