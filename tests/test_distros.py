import abc
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any, ClassVar, override
from unittest import mock

from moncic.distro import Distro, DistroFamily
from moncic.image import RunnableImage
from moncic.mock.session import MockSession
from moncic.unittest import MockRunLog, MoncicTestCase


class DistroFamilyTestsBase(MoncicTestCase):
    family: ClassVar[DistroFamily]

    def test_lookup_family(self) -> None:
        family = DistroFamily.lookup_family(self.family.name)
        self.assertIs(family, self.family)

    def test_str(self) -> None:
        self.assertEqual(str(self.family), self.family.name)

    def test_lookup_distro(self) -> None:
        for distro in self.family.distros:
            self.assertIs(self.family.lookup_distro(distro.full_name), distro)
            for alias in distro.aliases:
                self.assertIs(self.family.lookup_distro(alias), distro)


class DebianDistroFamilyTests(DistroFamilyTestsBase):
    family = DistroFamily.lookup_family("debian")


class UbuntuDistroFamilyTests(DistroFamilyTestsBase):
    family = DistroFamily.lookup_family("ubuntu")


class FedoraDistroFamilyTests(DistroFamilyTestsBase):
    family = DistroFamily.lookup_family("fedora")


class RockyDistroFamilyTests(DistroFamilyTestsBase):
    family = DistroFamily.lookup_family("rocky")


class CentosDistroFamilyTests(DistroFamilyTestsBase):
    family = DistroFamily.lookup_family("centos")


del DistroFamilyTestsBase


class DistroTestsBase(MoncicTestCase, abc.ABC):
    name: ClassVar[str]
    distro: ClassVar[Distro]

    @override
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.distro = DistroFamily.lookup_distro(cls.name)

    def session(self) -> MockSession:
        mconfig = self.config()
        return self.enterContext(self.mock_session(self.moncic(mconfig)))

    @abc.abstractmethod
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None: ...

    @abc.abstractmethod
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None: ...

    def assertUpdateScriptRPM(self, run_log: MockRunLog, packages: list[str]) -> None:
        script = run_log.assertPopScript("Upgrade container")
        self.assertEqual(
            script.lines,
            [
                "/usr/bin/systemctl mask --now systemd-resolved",
                "/usr/bin/dnf check-update -y -q || true",
                "/usr/bin/dnf upgrade -y -q",
                f"/usr/bin/dnf install -y -q {shlex.join(packages)}",
            ],
        )

    def get_osrelease_samples(self) -> list[dict[str, str]]:
        """Get sample parsed os-release files (if any) that should detect as this distro."""
        if self.distro.name == "testing":
            return []
        parsed = {"ID": self.distro.family.name}
        if self.distro.version is not None:
            parsed["VERSION_ID"] = self.distro.version
        return [parsed]

    def test_from_osrelease(self) -> None:
        for parsed in self.get_osrelease_samples():
            with self.subTest(parsed=parsed):
                self.assertIs(DistroFamily.from_osrelease(parsed, "invalid"), self.distro)

                with tempfile.TemporaryDirectory() as root_str:
                    root = Path(root_str)
                    path = root / "etc" / "os-release"
                    path.parent.mkdir(parents=True)
                    with path.open("wt") as fd:
                        print(f"ID={shlex.quote(parsed['ID'])}", file=fd)
                        if version_id := parsed.get("VERSION_ID"):
                            print(f"VERSION_ID={shlex.quote(version_id)}", file=fd)

                    self.assertIs(DistroFamily.from_path(root), self.distro)

    def test_get_podman_name(self) -> None:
        # Just call it and see it doesn't explode
        repo, tag = self.distro.get_podman_name()
        self.assertIsInstance(repo, str)
        self.assertIsInstance(tag, str)

    def test_bootstrap(self) -> None:
        session = self.session()
        path = self.workdir()
        self.distro.bootstrap(session.bootstrapper, path)
        self.assertBootstrapCommands(session.run_log, path)
        session.run_log.assertLogEmpty()

    def test_update(self) -> None:
        session = self.session()
        image = session.images.image(self.distro.full_name)
        assert isinstance(image, RunnableImage)

        image.update()
        session.run_log.assertPopFirst(f"{self.distro.full_name}: container start")
        self.assertUpdateCommands(session.run_log, Path("/test"))
        session.run_log.assertPopFirst(f"{self.distro.full_name}: container stop")
        session.run_log.assertLogEmpty()


class TestCentos7(DistroTestsBase):
    name = "centos7"

    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=7 install bash dbus iproute rootfiles yum"
            )
        )
        run_log.assertPopFirst(f"systemd-nspawn -D {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        script = run_log.assertPopScript("Upgrade container")
        self.assertEqual(
            script.lines,
            [
                "/usr/bin/yum check-update -y -q || true",
                "/usr/bin/yum upgrade -y -q",
                "/usr/bin/yum install -y -q bash dbus iproute rootfiles yum",
            ],
        )


class DebDistroTestsBase(DistroTestsBase):
    mirror: str
    custom_keyring = False
    install_verb: str = "satisfy"
    upgrade_verb: str = "full-upgrade"
    components: str | None = None

    @override
    def setUp(self) -> None:
        super().setUp()
        if self.custom_keyring:
            mock_response = mock.Mock()
            mock_response.content = b""
            self.enterContext(mock.patch("moncic.distro.debian.requests.get", return_value=mock_response))
            self.enterContext(mock.patch("moncic.distro.debian.subprocess.run"))

    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        match: list[str] = [
            r"(/usr/bin/eatmydata )?/usr/s?bin/(?:mmdebstrap|debootstrap)",
            re.escape("--include=apt-utils,bash,ca-certificates,dbus,eatmydata,iproute2,systemd"),
            re.escape(f"--variant=minbase"),
        ]
        if self.custom_keyring:
            match.append(r"--keyring=[^\.]+\.gpg")
        if self.components:
            match.append(re.escape(f"--components={shlex.quote(self.components)}"))
        match += [
            re.escape(f"{self.distro.name} {path} {self.mirror}"),
        ]
        run_log.assertPopFirst(re.compile(" ".join(match)))
        run_log.assertLogEmpty()

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        script = run_log.assertPopScript("Upgrade container")
        self.maxDiff = None
        self.assertEqual(
            script.lines,
            [
                "/usr/bin/apt-get update",
                "export DEBIAN_FRONTEND=noninteractive",
                "/usr/bin/apt-get --assume-yes --quiet --show-upgraded '-o Dpkg::Options::=\"--force-confnew\"'"
                f" {self.upgrade_verb}",
                "export DEBIAN_FRONTEND=noninteractive",
                "/usr/bin/apt-get --assume-yes --quiet --show-upgraded '-o Dpkg::Options::=\"--force-confnew\"'"
                f" {self.install_verb} apt-utils bash ca-certificates dbus eatmydata iproute2 systemd",
            ],
        )


class DebianDistroTestsBase(DebDistroTestsBase):
    mirror = "http://deb.debian.org/debian"


class TestStretch(DebianDistroTestsBase):
    mirror = "http://archive.debian.org/debian/"
    name = "stretch"
    custom_keyring = True
    install_verb = "install"


class TestBuster(DebianDistroTestsBase):
    name = "buster"
    install_verb = "install"


class TestBullseye(DebianDistroTestsBase):
    name = "bullseye"


class TestBookworm(DebianDistroTestsBase):
    name = "bookworm"


class TestTrixie(DebianDistroTestsBase):
    name = "trixie"


class TestTesting(DebianDistroTestsBase):
    name = "testing"


class TestSid(DebianDistroTestsBase):
    name = "sid"


class FedoraDistroTestsBase(DistroTestsBase):
    version: ClassVar[int]
    packages: list[str] = ["bash", "dbus", "dnf", "iproute", "rootfiles"]

    @override
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.name = f"fedora{cls.version}"

    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever={self.version} install {' '.join(self.packages)}"
            )
        )
        run_log.assertPopFirst(f"systemd-nspawn -D {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        self.assertUpdateScriptRPM(run_log, self.packages)


class TestFedora32(FedoraDistroTestsBase):
    version = 32


class TestFedora33(FedoraDistroTestsBase):
    version = 33


class TestFedora34(FedoraDistroTestsBase):
    version = 34


class TestFedora35(FedoraDistroTestsBase):
    version = 35


class TestFedora36(FedoraDistroTestsBase):
    version = 36


class TestFedora37(FedoraDistroTestsBase):
    version = 37


class TestFedora38(FedoraDistroTestsBase):
    version = 38


class TestFedora39(FedoraDistroTestsBase):
    version = 39


class TestFedora40(FedoraDistroTestsBase):
    version = 40


class TestFedora41(FedoraDistroTestsBase):
    version = 41
    packages = FedoraDistroTestsBase.packages + ["systemd"]


class TestFedora42(FedoraDistroTestsBase):
    version = 42
    packages = FedoraDistroTestsBase.packages + ["systemd"]


class TestRocky8(DistroTestsBase):
    name = "rocky8"

    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=8 install bash dbus dnf iproute rootfiles"
            )
        )
        run_log.assertPopFirst(f"systemd-nspawn -D {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        self.assertUpdateScriptRPM(run_log, ["bash", "dbus", "dnf", "iproute", "rootfiles"])


class TestRocky9(DistroTestsBase):
    name = "rocky9"

    @override
    def assertBootstrapCommands(self, run_log: MockRunLog, path: Path) -> None:
        run_log.assertPopFirst(
            re.compile(
                rf"/usr/bin/dnf -c \S+\.repo -y -q '--disablerepo=\*' --enablerepo=chroot-base '--disableplugin=\*'"
                rf" --installroot={path} --releasever=9 install bash dbus dnf iproute rootfiles"
            )
        )
        run_log.assertPopFirst(f"systemd-nspawn -D {path} /usr/bin/rpmdb --rebuilddb")

    @override
    def assertUpdateCommands(self, run_log: MockRunLog, path: Path) -> None:
        self.assertUpdateScriptRPM(run_log, ["bash", "dbus", "dnf", "iproute", "rootfiles"])


class UbuntuDistroTestsBase(DebDistroTestsBase):
    mirror = "https://archive.ubuntu.com/ubuntu/"
    components = "main,restricted,universe"


class TestXenial(UbuntuDistroTestsBase):
    name = "xenial"
    install_verb = "install"


class TestBionic(UbuntuDistroTestsBase):
    name = "bionic"
    install_verb = "install"


class TestFocal(UbuntuDistroTestsBase):
    name = "focal"


class TestHirsute(UbuntuDistroTestsBase):
    mirror = "https://old-releases.ubuntu.com/ubuntu/"
    name = "hirsute"


class TestImpish(UbuntuDistroTestsBase):
    mirror = "https://old-releases.ubuntu.com/ubuntu/"
    name = "impish"


class TestJammy(UbuntuDistroTestsBase):
    name = "jammy"


class TestKinetic(UbuntuDistroTestsBase):
    mirror = "https://old-releases.ubuntu.com/ubuntu/"
    name = "kinetic"


class TestLunar(UbuntuDistroTestsBase):
    mirror = "https://old-releases.ubuntu.com/ubuntu/"
    name = "lunar"


class TestMantic(UbuntuDistroTestsBase):
    mirror = "https://old-releases.ubuntu.com/ubuntu/"
    name = "mantic"


class TestNoble(UbuntuDistroTestsBase):
    name = "noble"


class TestOracular(UbuntuDistroTestsBase):
    name = "oracular"


class TestPlucky(UbuntuDistroTestsBase):
    name = "plucky"


del UbuntuDistroTestsBase
del DebianDistroTestsBase
del DebDistroTestsBase
del FedoraDistroTestsBase
del DistroTestsBase
