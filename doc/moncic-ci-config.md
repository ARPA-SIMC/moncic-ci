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
