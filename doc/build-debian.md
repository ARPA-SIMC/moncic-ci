# Building Debian packages

```
$ usage: monci ci [-h] [-v] [--debug] [-I IMAGEDIR] [-C CONFIG] [--extra-packages-dir EXTRA_PACKAGES_DIR] [--branch BRANCH] [-s SOURCE_TYPE] [-B file.yaml] [-a dir] [--source-only]
                [--shell] [--option OPTION]
                system [source]

positional arguments:
  system                name or path of the system used to build
  source                path or url of the repository or source package to build. Default: the current directory

options:
  -h, --help            show this help message and exit
  -v, --verbose         verbose output
  --debug               debugging output
  -I IMAGEDIR, --imagedir IMAGEDIR
                        path to the directory that contains container images. Default: from configuration file, or /var/lib/machines
  -C CONFIG, --config CONFIG
                        path to the Moncic-CI config file to use. By default, look in a number of well-known locations, see https://github.com/ARPA-SIMC/moncic-
                        ci/blob/main/doc/moncic-ci-config.md
  --extra-packages-dir EXTRA_PACKAGES_DIR
                        directory where extra packages, if present, are added to package sources in containers
  --branch BRANCH       branch to be used. Default: let 'git clone' choose
  -s SOURCE_TYPE, --source-type SOURCE_TYPE
                        name of the procedure used to run the CI. Use 'list' to list available options. Default: autodetect
  -B file.yaml, --build-config file.yaml
                        YAML file with build configuration
  -a dir, --artifacts dir
                        directory where build artifacts will be stored
  --source-only         only build source packages
  --shell               open a shell after the build
  --option OPTION, -O OPTION
                        key=value option for the build. See `-s list` for a list of available option for each build style
```

Run `monci ci` on a source using a Debian image to attempt to build a Debian
package. Moncic-CI will examine the source to find how it should be built,
using one of the strategies described in [Source styles](source-styles.md).
