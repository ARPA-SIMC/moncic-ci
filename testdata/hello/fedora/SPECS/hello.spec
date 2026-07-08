%global releaseno 1
# Note: define _srcarchivename in Travis build only.
%{!?srcarchivename: %global srcarchivename %{name}-%{version}-%{releaseno}}


Name:           hello
Version:        1.0
Release:        %{releaseno}%{dist}
Summary:        Minimal package used for Moncic-CI integration tests
Source0:        https://github.com/ARPA-SIMC/%{name}/archive/v%{version}-%{releaseno}.tar.gz#/%{srcarchivename}.tar.gz

License:        GPLv3
BuildArch:      noarch

%description
Minimal package used for integration tests

%global debug_package %{nil}

%prep
%autosetup -n %{srcarchivename}

%install
mkdir -p "%{buildroot}/%{_bindir}/"
cp hello "%{buildroot}/%{_bindir}/"

%clean

%files
%{_bindir}/hello

%changelog
* Fri May 30 2025 Enrico Zini <enrico@enricozini.org> - 1.0-1
- Created the package
