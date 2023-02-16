# Source styles

This the documentation of source styles you can use with `monci ci -s <name>`.

## debian-dir

Unpacked debian source
## debian-dsc

Debian source .dsc
## debian-gbp-release

Debian git working directory checked out to a tagged release branch.
## debian-gbp-test

Debian git working directory checked out to an untagged Debian branch.
## debian-gbp-upstream

Merge the current upstream working directory into the packaging branch for
the build distro.

We can attempt to build a source package by looking for a gbp-buildpackage
branch, and merging the current upstream branch into it
## debian-git-plain

Debian git working directory that does not use git-buildpackage.

If no tarball can be found, one is generated with `git archive`
## rpm-arpa

ARPA/SIMC git repository, building RPM packages using the logic previously
configured for travis
