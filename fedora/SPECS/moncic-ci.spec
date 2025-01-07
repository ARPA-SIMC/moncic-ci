%global releaseno 1
# Note: define _srcarchivename in Travis build only.
%{!?srcarchivename: %global srcarchivename %{name}-%{version}-%{releaseno}}


Name:           moncic-ci
Version:        0.16
Release:        %{releaseno}%{dist}
Summary:        Continuous integration tool and development helper

License:        GPLv2+
URL:            https://github.com/ARPA-SIMC/moncic-ci
Source0:        https://github.com/ARPA-SIMC/%{name}/archive/v%{version}-%{releaseno}.tar.gz#/%{srcarchivename}.tar.gz
BuildArch:      noarch

BuildRequires:  python3
BuildRequires:  python3-setuptools
BuildRequires:  python3-devel
BuildRequires:  python3-yaml
BuildRequires:  python3-ruamel-yaml
BuildRequires:  python3-tblib
BuildRequires:  python3-GitPython

# for tests
BuildRequires:  python3-pytest
BuildRequires:  python3-requests
BuildRequires:  systemd-container
BuildRequires:  btrfs-progs
BuildRequires:  dnf

Requires:       python3
Requires:       python3-yaml
Requires:       python3-ruamel-yaml
Requires:       btrfs-progs
Requires:       systemd-container
Requires:       python3-GitPython

# not strictly necessary, for formatting
Requires:       python3-texttable
Requires:       python3-coloredlogs

%description
Moncic CI manages lightweight containers for use with Continuous Integration
or to help developers target platforms different from the development machine.

It manages a pool of Operating System images that can be used to run shells,
commands, and builds.

It deduplicates common files across OS images, so one can have one image per
project on a developer machine with limited disk usage.

It has low startup times on containers, making it convenient for quick
development iterations: run tests on your code on another OS about as easily
as you would run it on your normal system, keeping iteration lags low.

%global debug_package %{nil}

%prep
%autosetup -n %{srcarchivename}

%build
%py3_build


%install
[ "%{buildroot}" != / ] && rm -rf "%{buildroot}"
%py3_install

%check
%pytest

%files
%{_bindir}/monci
%{python3_sitelib}/moncic*

%changelog
* Fri Oct 18 2024 Emanuele Di Giacomo <edigiacomo@arpae.it> - 0.16-1
- Fixed rpm-arpa build (#108)

* Tue Oct  8 2024 Daniele Branchini <dbranchini@arpae.it> - 0.15-1
- Redesigned source detection. Some of `monci ci --source-type list` styles have been chanegd
- Bug fixing (#106)
- Fixed deprecated methods on python 3.12
- Some more linter refactoring

* Fri May 10 2024 Daniele Branchini <dbranchini@arpae.it> - 0.14-1
- Added `--option include_source=true` to `monci ci` for Debian builds, to run
  `dpkg-buildpackage` with the `-sa` option. (#105)
- Validate `--option` based on available options for the selected build, and
  their type
- Fixed using `--source-type` with `rpm-arpa*` sources (#103)
- Added Fedora 39 and 40 to supported distros

* Tue Sep  5 2023 Emanuele Di Giacomo <edigiacomo@arpae.it> - 0.13-1
- `monci ci`: update/upgrade the container before a build, unless `--quick` is
  specified (#100)
- `monci lint`: static checks for sources (#101)

* Wed May 31 2023 Emanuele Di Giacomo <edigiacomo@arpae.it> - 0.12-1
- Build from tag when using a remote repository (#99)

* Mon May 22 2023 Daniele Branchini <dbranchini@arpae.it> - 0.11-1
- Do not accidentally skip source directory setup when specifying build type explicitly (#97)
- Always recreate all remote branches when cloning (#98)

* Wed May 17 2023 Daniele Branchini <dbranchini@arpae.it> - 0.10-3
- dnf builddep less quiet (#96)

* Tue May 16 2023 Daniele Branchini <dbranchini@arpae.it> - 0.10-2
- Deal with a missing /var/lib/machines in tests (#95)

* Mon May 15 2023 Daniele Branchini <dbranchini@arpae.it> - 0.10-1
- Removed reference to ARPA.system property (#92)
- Added check section to spec (#93)

* Mon May 15 2023 Daniele Branchini <dbranchini@arpae.it> - 0.9-1
- Fixed builds on RPM distros (#91)

* Wed May 10 2023 Daniele Branchini <dbranchini@arpae.it> - 0.8-1
- Support a variety of [source styles](doc/source-styles.md). See also
  [Building Debian Packages](doc/build-debian.md) (#63, #64)
- Support options specific to different [build styles](doc/build-styles.md)
- `ci --build-style` is replaced by `ci --source-type`, and it is autodetected
  by default
- Fixed a typo that broke DebianPlain builds
- Allow relative paths in `ci --artifacts`
- Reuse an existing tarball if one is found above the source directory, or in
  the artifacts directory (#48)
- Save a build log among output artifacts (#67)
- Added `ci --source-only` to do source-only builds
- Changes in `monci ci` command line: `-s` is now short for `--build-style`,
  and the system name is given as first argument (#73)
- Added experimental `monci analyze` that runs consistency checks on source
  directories
- Implemented `monci remove --purge` to also remove the config file (#74)
- Implemented `monci image [name] distro` to create a new image (#74)
- Implemented `monci image [name] extends` to create a new image (#74)
- Implemented `monci image [name] setup` to add a maintscript line to an image (#74)
- Implemented `monci image [name] edit` to edit an image's config file (#74)
- Implemented `monci image [name] install [packages...] to add packages to
  the image's package list
- Implemented `monci image [name] build-dep` to add build dependencies of a
  source directory (#76)
- Implemented `monci image [name] describe` to get a detailed description of
  how the image has been build (#77)
- Allow to configure a list of packages in [image configuration](doc/image-config.md)
  instead of manually invoking the package manager in the maintscript
- Propagate options to subcommands, so they can be used anywhere in the command
  line
- Try out easier to type machine names (#78)
- Set hostname of containers to the machine name
- `monci images` does not require root to run
- Create the `--artifacts` directory if it does not exist
- Allow to use a YAML file to customize the build. See [YAML configuration for
  CI builds](doc/build-config.md)
- Always print JSON build results even if the build failed
- Added [post-build actions](doc/post-build.actions.md) (#85)
- Added Rocky Linux 9, Fedora 37 and Fedora 38 to supported builds

* Fri Sep  2 2022 Daniele Branchini <dbranchini@arpae.it> - 0.7-1
- Removed support for a btrfs filesystem in a file (#41)
- Prototype `Builder` for building Debian packages (#47)
- Added option `--bind-volatile`, working as `--bind-ro` plus a temporary
  writable overlay (#50)
- Added option `--workdir-volatile` working as `--workdir` but with a volatile
  mount (#50)
- Use volatile mounts for CI (#51)
- Added config option `build_artifacts_dir` and command line option `monci ci
  --artifacts` to collect build artifacts in a directory (#10)
- Added option `debcachedir` to point to a directory where downloaded `.deb`
  files can be cached across container runs (#52)
- Added option `extra_packages_dir` and `--extra-packages-dir` to provide extra
  packages as dependencies for builds (#49)
- Added `echo 7 > /etc/yum/vars/releasever` to Centos7 bootstrap
- Added `monci ci --shell` to open a shell in the container after the build

* Fri Jun  3 2022 Daniele Branchini <dbranchini@arpae.it> - 0.6-1
- Fixed network issues with systemd-resolved based images (Fedora 36)

* Wed May 25 2022 Daniele Branchini <dbranchini@arpae.it> - 0.5-1
- Fixed bootstrapping on non-btrfs filesystems (#44)

* Fri May 13 2022 Daniele Branchini <dbranchini@arpae.it> - 0.4-4
- Arpa builder search SPECS both in */SPECS and ./ (#42)

* Mon May  9 2022 Daniele Branchini <dbranchini@arpae.it> - 0.4-2
- Added Fedora 36 distro

* Mon May  9 2022 Daniele Branchini <dbranchini@arpae.it> - 0.4-1
- Allow to store image configuration separately from images (#33)
- Get systemd version from systemcl instead of systemd (#40)

* Thu Apr 28 2022 Daniele Branchini <dbranchini@arpae.it> - 0.3-1
- Default for imagedir changed from `./images` to `/var/lib/machines` (#25)
- Added a `tmpfs` configuration, both global and per-image, to use `tmpfs`
  backing for ephemeral images instead of btrfs snapshots. If the machine is
  configured with enough ram and swap, it makes for faster CI runs (#27)
- Support non-btrfs image storage, by forcing ephemeral images to use `tmpfs`
  backing instead of btrfs snapshots
- Add `-C`/`--config` option to specify a config file from command line (#34)
- Made non-ephemeral containers transactional on BTRFS: updates are run on a
  snapshot of the OS image, which is swapped with the original if the operation
  succeeds, or removed without changing the original if it fails (#29)
- Run containers with `--suppress-sync=yes` on systemd >= 250 (#28)
- Run commands in container with /dev/null redirectd to stdin, instead of stdin
  being a closed file descriptor (#37)
- Fixed selection of build-dep command in ARPA-style builds (#38)

* Thu Mar 24 2022 Daniele Branchini <dbranchini@arpae.it> - 0.2-1
- Implemented simple deduplication of files with the same name and size across
  OS images. (#19)
- Deduplication is triggered automatically at the end of `monci update` (#19)
- Allow to configure a compression property to use when creating BTRFS
  subvolumes
- Exit with an appropriate error message instead of a traceback when asking for
  `shell` or `run` on an image that has not yet been bootstrapped
- If bootstrap is interrupted by keyboard interrupt, remove the subvolume
  instead of leaving a partially built OS image
- If imagedir points to a btrfs filesystem on a file, automatically mount it an
  unmount it as needed (#21)
- `fstrim(8)` disk usage on an images file if configured, or if it can be
  detected to be on an SSD (#21)
- Automatically reexec with sudo if permissions are needed (#23)
- Do not run the default upgrade command if a maintscript is provided
- Allow using `.tar.xz` and `.tar` as cached distribution images
- Reduce yum/dnf verbosity on bootstrap

* Mon Mar  7 2022 Daniele Branchini <dbranchini@arpae.it> - 0.1-1
- First build
