# Testing on another distro

You are trying to fix a test case that fails on Rocky Linux 8, but your
development machine is Debian Stable.

It would be nice to have a way to quickly run `make check` on Rocky 8 locally,
without needing to log to a remote machine. Here's how to do it.


## Create a custom image

Create `mycode.yaml` in the Moncic-CI images directory:

```yaml
extends: rocky8
forward-user: myusername
maintscript: |
   dnf install my-build-dependencies
```

Then run `sudo monci bootstrap mycode --verbose` and watch it being built.

This will also create a `rocky8` OS image, and snapshot it as a base for
`mycode`: that way you can have many custom distribution that snapshot the same
base Rocky8 distribution.


## Run `make check`

`monci run` can run an arbitrary command in a chroot, we'll use it to run make
check:

```
sudo monci run mycode --workdir=. make check
```

This will run `make check` on the current directory, but in the `mycode` OS
image.

You can also run make check on a freshly cloned copy of your repository:

```
sudo monci run mycode --clone=. make check
```

All these commands have a very quick startup time, so you can run this instead
of `make check` as you try to debug your issue.


## Using out of tree builds

Most compiled languages support building out of tree, and you can take
advantage of this to have a build directory dedicated to Rocky Linux 8,
different than the one you use for your local builds.

How to do it with *meson*:

```
sudo moncy run mycode -w . meson setup build-rocky8
sudo moncy run mycode -w . ninja -C build-rocky8
```

How to do it with *cmake*:

```
mkdir build-rocky8
sudo moncy run mycode -w build-rocky8 cmake ..
sudo moncy run mycode -w build-rocky8 make check
```

How to do it with *autotools*:

```
mkdir build-rocky8
sudo moncy run mycode -w build-rocky8 ../configure
sudo moncy run mycode -w build-rocky8 make check
```
