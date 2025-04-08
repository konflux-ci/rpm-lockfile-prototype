# What is this

This configuration file will find out what packages are needed to install
`vim-enhanced` into a `fedora:rawhide` container.

The exact repository URL is constructed using a label obtained from the base image.

Which image to use is read from the Containerfile.


# How to run this

```
$ rpm-lockfile-prototype rpms.in.yaml
```
