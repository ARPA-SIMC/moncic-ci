%global releaseno 1
# Note: define _srcarchivename in Travis build only.
%{!?srcarchivename: %global srcarchivename %{name}-%{version}-%{releaseno}}


Name:           moncic-ci
Version:        0.1
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
* Mon Mar  7 2022 Daniele Branchini <dbranchini@arpa.emr.it> - 0.1-1
- First build
