# What is this

This is an example of a multi stage container build. Two of the three stages
install RPMs, so we need two independent config files. Each one selects
appropriate build stage using a name.

Repositories are configured using a .repo file.

# How to run this

```
$ rpm-lockfile-prototype develop.rpms.in.yaml
$ rpm-lockfile-prototype runtime.rpms.in.yaml
```
