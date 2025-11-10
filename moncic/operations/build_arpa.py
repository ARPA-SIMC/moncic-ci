import logging
import shlex
from pathlib import Path
from typing import Any, override

from moncic.container import Container
from moncic.runner import UserConfig
from moncic.source.rpm import RPMSource
from moncic.utils.script import Script

from .build import Builder

log = logging.getLogger(__name__)


class RPMBuilder(Builder):
    """
    Build RPM packages
    """

    source: RPMSource

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from ..distro.rpm import DnfDistro, YumDistro

        if isinstance(self.image.distro, YumDistro):
            self.builddep = ["yum-builddep"]
        elif isinstance(self.image.distro, DnfDistro):
            self.builddep = ["dnf", "builddep"]
        else:
            raise RuntimeError(f"Unsupported distro: {self.image.distro.name}")
        self.results.name = self.source.specfile_path.name.removesuffix(".spec")

        self.guest_rpmbuild_path = Path("/root/rpmbuild")

    # @host_only
    # def get_build_deps(self) -> list[str]:
    #     with self.container() as container:
    #         # Build run config
    #         run_config = container.config.run_config()

    #         return container.run_callable(
    #                 self.get_build_deps_in_container,
    #                 run_config).result()

    # @guest_only
    # def get_build_deps_in_container(self) -> list[str]:
    #     assert isinstance(self.source, RPMSource)
    #     specfile = self.source.path / self.source.specfile_path
    #     res = subprocess.run(["/usr/bin/rpmspec", "--parse", specfile], stdout=subprocess.PIPE, text=True, check=True)
    #     packages = []
    #     for line in res.stdout.splitlines():
    #         if line.startswith("BuildRequires: "):
    #             packages.append(line[15:].strip())
    #     return packages


class ARPABuilder(RPMBuilder):
    """
    ARPA/SIMC builder, building RPM packages using the logic previously
    configured for travis
    """

    @override
    def build(self, container: Container) -> None:
        assert self.results.name is not None
        script = Script(f"Build {self.source.name}", user=UserConfig.root())
        script.run(
            ["mkdir", "-p"]
            + [
                (self.guest_rpmbuild_path / name).as_posix()
                for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS")
            ],
            description="Create rpbmuild directory tree",
        )
        guest_specfile_path = self.guest_source_path / self.source.specfile_path
        script.run(self.builddep + ["-y", guest_specfile_path.as_posix()], description="Install build dependencies")

        guest_rpmbuild_sources_dir = self.guest_source_path / "fedora/SOURCES"

        if self.source.specfile_path.is_relative_to("fedora/SPECS/"):
            # Convenzione SIMC per i repo upstream
            if (self.host_source_path / "fedora" / "SOURCES").is_dir():
                script.run_unquoted(
                    f"cp -r fedora/SOURCES/* {self.guest_rpmbuild_path / 'SOURCES'}", cwd=self.guest_source_path
                )

            script.run(["git", "config", "--global", "--add", "safe.directory", self.guest_source_path.as_posix()])
            guest_source_tarball = guest_rpmbuild_sources_dir / f"{self.results.name}.tar.gz"
            script.run(
                [
                    "git",
                    "archive",
                    f"--prefix={shlex.quote(self.results.name)}/",
                    "--format=tar.gz",
                    "-o",
                    guest_source_tarball.as_posix(),
                    "HEAD",
                ],
                cwd=self.guest_source_path,
            )
            script.run(
                [
                    "spectool",
                    "-g",
                    "-R",
                    "--define",
                    f"srcarchivename {self.results.name}",
                    guest_specfile_path.as_posix(),
                ]
            )
            if self.config.source_only:
                build_arg = "-br"
            else:
                build_arg = "-ba"
            script.run(
                [
                    "rpmbuild",
                    build_arg,
                    "--define",
                    f"srcarchivename {self.results.name}",
                    guest_specfile_path.as_posix(),
                ]
            )
        else:
            # Convenzione SIMC per i repo con solo rpm
            script.run_unquoted(f"cp *.patch {guest_rpmbuild_sources_dir}/", cwd=guest_rpmbuild_sources_dir)
            script.run(["spectool", "-g", "-R", guest_specfile_path.as_posix()])
            script.run(["rpmbuild", "-ba", guest_specfile_path.as_posix()])

        self.results.scripts.append(script)
        container.run_script(script)
        self.results.success = True

    @override
    def collect_artifacts_script(self) -> Script:
        script = super().collect_artifacts_script()
        dest = Path("/srv/moncic-ci/artifacts")
        patterns = (
            "RPMS/*/*.rpm",
            "SRPMS/*.rpm",
        )
        basedir = Path("/root/rpmbuild")
        for pattern in patterns:
            script.run_unquoted(f"cp --reflink=auto {basedir}/{pattern} {shlex.quote(dest.as_posix())}")
        return script
