# Reproducible test sessions

You want to try reproducing a bug, here's how you can go about it.

You can use `monci image` to quickly create and setup a system image to use for
quick development iterations.

## Create a system image for debugging

Use `monci image [name] extends` to create a new image based on an existing one (you
can use `distro` to bootstrap a new image from scratch):

```
monci image testimg extends bookworm
```

Use `monci image [name] install` to install packages you need:

```
monci image testimg install build-essential libfoo-dev
```

Use `monci image [name] setup` to run extra maintenance commands:

```
monci image testimg setup /usr/bin/sed -i '/^tsflags=/d' /etc/dnf/dnf.conf
```

## Test your code

You can use `monci shell testimg -w .` to open a shell in the current directory
to try things. You can use `-W` instead of `-w` to make changes ephemeral also
in the source directory.

However, since we are talking many iterations, you can create a `test` script
at the top of your source directory, and run:

```
monci shell testimg -w . ./test
```

Edit the script as you iterate, to save keystrokes.

## File a bug report

Once you manage to reproduce the issue, in `~/.config/moncic-ci/testimg.yaml`
you have all the commands needed to set up the test environment, and in your
script you have the commands needed to reproduce the problem in it.

That's very helpful information for putting together a well written bug report.

## Cleanup

In the end, you can delete the image and its configuration:

```
monci remove --purge testimg
```
