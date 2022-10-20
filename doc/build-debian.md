# Building Debian packages

```
$ ./monci ci --help
usage: monci ci [-h] [-v] [--debug] [-I IMAGEDIR] [-C CONFIG] [--extra-packages-dir EXTRA_PACKAGES_DIR] [--branch BRANCH] [-s SYSTEM] [-b BUILD_STYLE] [-a dir] [--shell] [repo]

positional arguments:
  repo                  path or url of the repository to build. Default: the current directory

options:
  -h, --help            show this help message and exit
  -v, --verbose         verbose output
  --debug               verbose output
  -I IMAGEDIR, --imagedir IMAGEDIR
                        path to the directory that contains container images. Default: from configuration file, or /var/lib/machines
  -C CONFIG, --config CONFIG
                        path to the Moncic-CI config file to use. By default, look in a number of well-known locations, see https://github.com/ARPA-SIMC/moncic-ci/blob/main/doc/moncic-ci-
                        config.md
  --extra-packages-dir EXTRA_PACKAGES_DIR
                        directory where extra packages, if present, are added to package sources in containers
  --branch BRANCH       branch to be used. Default: let 'git clone' choose
  -s SYSTEM, --system SYSTEM
                        name or path of the system used to build
  -b BUILD_STYLE, --build-style BUILD_STYLE
                        name of the procedure used to run the CI. Default: 'travis'
  -a dir, --artifacts dir
                        directory where build artifacts will be stored
  --shell               open a shell after the build
```

In short, to build Debian packages use `--build-style Debian`, and it should do the right thing.

Using the `Debian` build style, Moncic-CI will examine the source to find how
it should be built, using one of the strategies described below. Alternatively,
you can use the strategy name directly to skip autodetectection.


## `DebianPlain` strategy

This will build the source package using `dpkg-buildpackage -S --no-sign --no-pre-clean`.

This is autoselected if the `debian/` directory exists, but there is no
`debian/gbp.conf`.

Currently, the upstream tarball is always generated using `git archive -f …
HEAD`, as a quick hack that should be replaced by reusing an upstream tarball
if it already exists.


## `DebianGBP` strategy

This will build the package using [git-buildpackage](https://honk.sigxcpu.org/piki/projects/git-buildpackage/).

Moncic-CI will analyze the git repository further to choose one of the
`git-buildpackage`-based strategies below, which can also be used directly with
`--build-style`.


## `DebianGBPRelease` strategy

This is autoselected if the git commit being built is a git tag, and it
contains a `debian/` directory.

`git-buildpackage` is invoked with `--git-upstream-tree=tag`, to build the
release version of a package.


## `DebianGBPTestUpstream` strategy

<!-- TODO
        if repo.head.commit.hexsha in [t.commit.hexsha for t in repo.tags]:
            if os.path.isdir(os.path.join(srcdir, "debian")):
                # If branch to build is a tag, build a release from it
                return DebianGBPRelease.create(system, srcdir)
            else:
                # There is no debian/directory, the current branch is upstream
                return DebianGBPTestUpstream.create(system, srcdir)
        else:
            if os.path.isdir(os.path.join(srcdir, "debian")):
                # There is a debian/ directory, find upstream from gbp.conf
                return DebianGBPTestDebian.create(system, srcdir)
            else:
                # There is no debian/directory, the current branch is upstream
                return DebianGBPTestUpstream.create(system, srcdir)
-->

## `DebianGBPTestDebian` strategy

This is autoselected if the git commit being built is not a tag, and it
contains a `debian/` directory.

The upstream branch is read from `debian/gbp.conf`, and merged into the current
branch. After which, git-buildpackage is run with `--git-upstream-tree=branch`.

This is used to test the Debian packaging against its intended upstream branch.
