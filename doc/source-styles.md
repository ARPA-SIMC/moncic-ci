# Source styles

This the documentation of source styles you can use with `monci ci -s <name>`.

## debian-dir

Unpacked debian source
## debian-dsc

Debian source .dsc
## debian-gbp-release

Debian git working directory checked out to a tagged release branch.

This is autoselected if the git commit being built is a git tag, and it
contains a `debian/` directory.

`git-buildpackage` is invoked with `--git-upstream-tree=tag`, to build the
release version of a package.
## debian-gbp-test

Debian git working directory checked out to an untagged Debian branch.

This is autoselected if the git commit being built is not a tag, and it
contains a `debian/` directory.

The upstream branch is read from `debian/gbp.conf`, and merged into the
current branch. After which, git-buildpackage is run with
`--git-upstream-tree=branch`.

This is used to test the Debian packaging against its intended upstream
branch.
## debian-gbp-upstream

Merge the current upstream working directory into the packaging branch for
the build distro.

This will look for a packaging branch corresponding to the distribution
used by the current build image (for example, `debian/bullseye` when
running on a Debian 11 image, or `ubuntu/jammy` when running on an Ubuntu
22.04 image.

It will then check it out, merge the source branch into it, and build the
resulting package.

This is autoselected if either:

* the git commit being built is a git tag but does not contain a `debian/`
  directory (i.e. testing packaging of a tagged upstream branch)
* the git commit being built is not a git tag, and does not contain a `debian/`
  directory (i.e. testing packaging of an upstream branch)
## debian-git-plain

Debian git working directory that does not use git-buildpackage.

This is autoselected if the `debian/` directory exists, but there is no
`debian/gbp.conf`.

An upstream `orig.tar.gz` tarball is searched on `..` and on the artifacts
directory, and used if found.

If no existing upstream tarball is found, one is generated using
`git archive HEAD . ":(exclude)debian"`, as a last-resort measure.
## rpm-arpa

ARPA/SIMC git repository, building RPM packages using the logic previously
configured for travis
