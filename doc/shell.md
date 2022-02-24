# Trying things out on a shell

It can be very handy to get a shell on a different distribution to try things
out, install packages, fiddle with configurations, and in the end throw
everything away.

You can do that with `monci shell`:

```
$ monci shell --help
[sudo] password for enrico: 
usage: monci shell [-h] [-v] [--debug] [-I IMAGEDIR] [--maintenance] [--workdir WORKDIR | --checkout CHECKOUT] [--bind BIND] [--bind-ro BIND_RO] [-u] [-r] system

positional arguments:
  system                name or path of the system to use

optional arguments:
  -h, --help            show this help message and exit
  -v, --verbose         verbose output
  --debug               verbose output
  -I IMAGEDIR, --imagedir IMAGEDIR
                        path to the directory that contains container images. Default: ./images
  --maintenance         do not run ephemerally: changes will be preserved
  --workdir WORKDIR     bind mount (writable) the given directory in /root
  --checkout CHECKOUT, --co CHECKOUT
                        checkout the given repository (local or remote) in the chroot
  --bind BIND           option passed to systemd-nspawn as is (see man systemd-nspawn) can be given multiple times
  --bind-ro BIND_RO     option passed to systemd-nspawn as is (see man systemd-nspawn) can be given multiple times
  -u, --user            create a shell as the current user before sudo (default is root, or the owner of workdir)
  -r, --root            create a shell as root (useful if using workdir and still wanting a root shell)
```

For example:

## Play around in a shell

```
sudo monci shell rocky8
```

This gives you a root shell.

You can install packages, change configuration, try things out as you need. Any
filesystem changes will be discarded when you quit the shell.


## Work on your code on another OS

```
sudo monci shell rocky8 --workdir=.
```

This makes sure your user exists in the test system, and bind mounts the
current directory into it.

Changes to the current directory will be preserved, and any other changes to
the filesystem will be discarded when you quit the shell.

You can add `--root` to run the shell as root instead of as the current user.


## Work on a copy of your code on another OS

```
sudo monci shell rocky8 --checkout=https://github.com/ARPA-SIMC/moncic-ci.git
```

The checkout is done on a temporary directory before starting the container,
and then bind mounted inside it. This means that for `--checkout` you can use
anything `git clone` understands, and can refer to the host file system.

For example, this also works, and allows you to swiftly have a go on a
throwaway clone of the current repository:

```
sudo monci shell rocky8 --checkout=.
```
