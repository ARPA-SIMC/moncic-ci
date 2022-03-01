# OS images in a file

If a btrfs partition is not convenient for you to have, you can use a file
instead, and Moncic-CI will know how to mount it, unmount it, and maintain it
as needed.

This will introduce a bit of lag in `monci` startup/shutdown, so using a
partition instead of a file will give you a snappier user experience.


## Creating the file:

If you use a SSD, this will only allocate disk data as needed:

```
truncate --size=10G images
```

or if you use a spinning disk, this will reserve space for the filesystem, and
the operating system will try to have it as contiguous as possible:

```
fallocate --size=10G images
```

Then, format it with `/sbin/btrfs images`, and it's ready.


## Using the file

If Moncic-CI sees that the image directory is actually a file, it will mount it
on a temporary directory and unmount it when it's done. Things are done so that
you can run multiple concurrent commands without interference.


## Reclaiming unused space

If you use a SSD and fragmentation of disk space is not a real issue, you can
reclaim unused disk space in the btrfs file with `fstrim`:

```
mount images /mnt
fstrim /mnt
umount /mnt
```

This will check what blogs on `images` are not in use, and deallocate them in
the `images` file, leaving "holes" in their places.

Moncic-CI will do this automatically if it can detect that the images file is
on a SSD. This feature can be forced on or off with the [`trim_image_file`
configuration option](moncic-ci-config.md).
