#!/bin/bash
set -ex

image=$1

if [[ $image =~ ^centos7 ]]
then
    pkgcmd="yum"
    builddep="yum-builddep"
    sed -i '/^tsflags=/d' /etc/yum.conf
    yum install -y epel-release
    yum install -y @buildsys-build
    yum install -y yum-utils
    yum install -y git
    yum install -y rpmdevtools
    yum install -q -y yum-plugin-copr
    yum copr enable -q -y simc/stable epel-7
    yum upgrade -q -y
elif [[ $image =~ ^centos8 ]]
then
    pkgcmd="dnf"
    builddep="dnf builddep"
    sed -i '/^tsflags=/d' /etc/dnf/dnf.conf
    dnf install -q -y epel-release
    dnf install -q -y 'dnf-command(config-manager)'
    dnf config-manager --set-enabled powertools
    dnf groupinstall -q -y "Development Tools"
    dnf install -q -y 'dnf-command(builddep)'
    dnf install -q -y git
    dnf install -q -y rpmdevtools
    dnf copr enable -y simc/stable
    dnf upgrade -q -y
elif [[ $image =~ ^fedora ]]
then
    pkgcmd="dnf"
    builddep="dnf builddep"
    rpmdb --rebuilddb
    sed -i '/^tsflags=/d' /etc/dnf/dnf.conf
    dnf install -y --allowerasing @buildsys-build
    dnf install -y 'dnf-command(builddep)'
    dnf install -y git
    dnf install -q -y rpmdevtools
    dnf copr enable -q -y simc/stable
    dnf upgrade -q -y
fi
