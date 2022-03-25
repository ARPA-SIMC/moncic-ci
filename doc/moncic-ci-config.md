# Configuration file for moncic-ci

Moncic-CI supports reading defaults from a configuration file. Currently only
`imagedir` is supported, which provides a default for the `--imagedir` command
line argument.


## Configuration file locations

Moncic-CI looks in these locations in sequence to find a `moncic-ci.yaml`
configuration file, and the first location where a file is found gives the file
that is used:

1. In `.git/moncic-ci.yaml` of the current git repository. Moncic-CI uses `git
   rev-parse --git-dir` to find the right `.git` directory
2. In `$XDG_CONFIG_HOME/moncic-ci/moncic-ci.yaml`. `XDG_CONFIG_HOME` defaults
   to `~/.config`.
3. In `/etc/moncic-ci.yaml`


## Configuration file contents

Example:

```yaml
imagedir: ~/.local/share/moncic-ci/
```

The only keyword currently supported is:

* `imagedir`: directory that contains OS images. Note that this needs to be on
  a BTRFS file system. It will be expanded with `os.path.expanduser`, so `~`
  and `~user` notations are supported.
* `compression`: [btrfs compression attribute](https://btrfs.wiki.kernel.org/index.php/Compression)
  to set on OS image subvolumes when they are created. The value is the same as
  can be set by `btrfs property set compression`. By default, nothing is set.
* `trim_image_file`: if set to True, automatically run fstrim on the image file
  after regular maintenance. If set to False, do not do that. By default,
  Moncic-CI will run fstrim if it can detect that the image file is on a SSD.
  This is only relevant when [using a file to store OS images](btrfs-on-file.md).
* `auto_sudo`: Automatically reexec with sudo if permissions are needed.
  Default: true
* `tmpfs`: Use a tmpfs overlay for ephemeral containers instead of btrfs
  snapshots. Default: false, or true if OS images are not on btrfs
