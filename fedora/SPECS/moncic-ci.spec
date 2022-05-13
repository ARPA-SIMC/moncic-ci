%global releaseno 3
# Note: define _srcarchivename in Travis build only.
%{!?srcarchivename: %global srcarchivename %{name}-%{version}-%{releaseno}}


Name:           moncic-ci
Version:        0.4
Release:        %{releaseno}%{dist}
Summary:        Continuous integration tool and development helper

License:        GPLv2+
URL:            https://github.com/ARPA-SIMC/moncic-ci
Source0:        https://github.com/ARPA-SIMC/%{name}/archive/v%{version}-%{releaseno}.tar.gz#/%{srcarchivename}.tar.gz

BuildRequires:  python3
BuildRequires:  python3-setuptools
BuildRequires:  python3-devel
BuildRequires:  python3-yaml

Requires:       python3
Requires:       python3-yaml
Requires:       btrfs-progs
Requires:       systemd-container

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


%files
%{_bindir}/monci
%{python3_sitelib}/moncic*

%changelog
* Fri May 13 2022 Daniele Branchini <dbranchini@arpae.it> - 0.4-3
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
