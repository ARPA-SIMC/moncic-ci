#!/bin/bash
set -exo pipefail

image=$1

if [[ $image == centos:7 ]]
then
    pkgcmd="yum"
    builddep="yum-builddep"
elif [[ $image == centos:8 ]] || [[ $image = fedora:[0-9][0-9] ]]
then
    pkgcmd="dnf"
    builddep="dnf builddep"
fi

specfile=$(find . -name "*.spec" | head -n1)
[[ -z $specfile ]] && { echo "Specfile not found"; exit 1; }

$builddep -q -y $specfile

pkgname=$(basename $specfile .spec)
mkdir -p ~/rpmbuild/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

if [[ $specfile =~ ^./fedora/SPECS/ ]]
then
    # Convenzione SIMC per i repo upstream
    [[ -d fedora/SOURCES ]] && find fedora/SOURCES -type f -print0 | xargs -0 -I{} cp {} ~/rpmbuild/SOURCES/
    git archive --prefix=$pkgname/ --format=tar HEAD | gzip -c > ~/rpmbuild/SOURCES/$pkgname.tar.gz
    spectool -g -R --define "srcarchivename $pkgname" $specfile
    rpmbuild -ba --define "srcarchivename $pkgname" $specfile
else
    # Convenzione SIMC per i repo con solo rpm
    find . -type f -name "*.patch" -maxdepth 1 -print0 | xargs -0 -I{} cp {} ~/rpmbuild/SOURCES/
    spectool -g -R $specfile
    rpmbuild -ba $specfile
fi

