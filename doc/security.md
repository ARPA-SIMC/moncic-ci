# Security considerations

Moncic-CI is not intended to be a tool for containing malicious code.

Both Moncic-CI and systemd-nspawn that is used as a backend, are only designed
to protect against accidental destruction. Containing malicious code is out of
scope for this project.

Most uses of Moncic-CI require running as root, generally using sudo.
Moncic-CI, when running as root, will look for `$SUDO_UID` and `$SUDO_GID` in
the environment, and if found will try to run as much code as possible as that
user, to prevent at last some of the potential bugs of Moncic-CI to have
root-level side effects.

Here are a number of things worth being aware of:

* Running Moncic-CI is pretty much equivalent as running a root shell.

  For example, one could run `monci shell <container> -w /etc` to get a root
  shell that can edit the host machine user database.

* Containerization is limited: the user namespace is shared, to be able to work
  on a working directory running with the same user ID as its owner.

* Containerization is limited: the network namespace is shared, to be able to
  access the network from inside the container and avoid the extra lag of
  obtaining an IP for a private network each time a container is started up.

  As a consequence, a process running in a container can connect to anything
  listening on any local network interface in the host system, including
  localhost.

  This may change if one can find a way to use a private network access, with
  access to the internet, a very low latency at container startup, and without
  requiring complex network configuration as a prerequisite to running
  Moncic-CI.
