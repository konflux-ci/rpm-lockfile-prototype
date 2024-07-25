# What is this?

This repo contains a proof-of-concept tool that implements most of the
requirements for KONFLUX-2110.

**There are no stability guarantees.**

The output should generally be compatible with what is being implemented in
cachi2, but there are some differences, like adding additional `sourcerpm` key
for binary packages to make it easier to map the package to corresponding
source.

## Installation

Install with pip directly from Git:

```
$ pip install --user https://github.com/konflux-ci/rpm-lockfile-prototype/archive/refs/heads/main.zip
```

Or latest released version:

```
$ pip install --user https://github.com/konflux-ci/rpm-lockfile-prototype/archive/refs/tags/v0.5.1.tar.gz
```


# How to run this from git

The tool requires on dnf libraries, which are painful to get into virtual
environment. Enabling system packages makes it easier.

Additionally, the tool requires skopeo and rpm to be available on the system.

```
$ python -m venv venv --system-site-packages
$ . venv/bin/activate
(venv) $ python -m pip install -e .
(venv) $ rpm-lockfile-prototype --help
usage: rpm-lockfile-prototype [-h]
                              [-f CONTAINERFILE | --image IMAGE | --local-system | --bare | --rpm-ostree-treefile RPM_OSTREE_TREEFILE]
                              [--flatpak] [--debug] [--arch ARCH] [--outfile OUTFILE]
                              [--print-schema] [--allowerasing]
                              INPUT_FILE

positional arguments:
  INPUT_FILE

options:
  -h, --help            show this help message and exit
  -f CONTAINERFILE, --containerfile CONTAINERFILE
                        Load installed packages from base image specified in
                        Containerfile and make them available during dependency
                        resolution.
  --image IMAGE         Use rpmdb from the given image.
  --local-system        Resolve dependencies for current system.
  --bare                Resolve dependencies as if nothing is installed in the target
                        system.
  --rpm-ostree-treefile RPM_OSTREE_TREEFILE
  --flatpak             Determine the set of packages from the flatpak: section of
                        container.yaml.
  --debug
  --arch ARCH           Run the resolution for this architecture. Can be specified
                        multiple times.
  --outfile OUTFILE
  --print-schema        Print schema for the input file to stdout.
  --allowerasing        Allow erasing of installed packages to resolve dependencies.
(venv) $
```

# What's the `INPUT_FILE`

The input file tells this tool where to look for RPMs and what packages to
install. If not specified, `rpms.in.yaml` from current working directory will
be used. It's a yaml file with following structure.

```yaml
contentOrigin:
  # Define at least one source of packages, but you can have as many as you want.
  repos:
    # List of objects with repoid and baseurl
    - repoid: fedora
      baseurl: https://kojipkgs.fedoraproject.org/compose/rawhide/latest-Fedora-Rawhide/compose/Everything/$basearch/os/
      # You can list any option that would be in .repo file here too.
      # For example sslverify, proxy or excludepkgs might be of interest
  repofiles:
    # Either local path or url pointing to .repo file
    - ./c9s.repo
    - https://example.com/updates.repo
  composes:
    # If your environment uses Compose Tracking Service (https://pagure.io/cts/)
    # and you define environment variable CTS_URL, you can look up repos from
    # composes either by compose ID or by finding latest compose matching some
    # filters. Fedora doesn't use CTS, so the examples are just for illustration
    # and do not work.
    - id: Fedora-Rawhide-20240411.n.0
    - latest:
        release_short: Fedora
        release_version: Rawhide
        release_type: ga
        tag: nightly

packages:
  # list of rpm names to resolve
  - vim-enhanced

reinstallPackages: []
  # list of rpms already provided in the base image, but which should be
  # reinstalled

arches:
  # The list of architectures can be set in the config file. Any `--arch` option set
  # on the command line will override this list.
  - aarch64
  - x86_64

context:
    # Alternative to setting command line options. Usually you will only want
    # to include one of these options, with the exception of `flatpak` that
    # can be combined with `image`, `containerfile`, or `bare`
    image: registry.fedoraproject.org/fedora:latest
    containerfile: Containerfile.fedora
    flatpak: true
    bare: true
    localSystem: true
    rpmOstreeTreefile: centos-bootc/centos-bootc.yaml
```

# What does this do

High-level overview: given a list of packages, repo urls and installed
packages, resolve all dependencies for the packages.

There are three options for how the installed packages can be handled.

1. Resolve in the current system (`--local-system`). This is probably not
   useful for anything.

2. Resolve in empty root (`--bare`, `--rpm-ostree-treefile`). This is useful
   when the final image is starting from scratch, like a base image or ostree.

   When using rpm-ostree treefile, the list of packages in input file is not
   needed. The tool will try to get list of required packages from the
   treefile. The support for some stanzas in the treefile is currently missing,
   so some packages may not be discovered.

3. Extract installed packages from a container image. This would be used for
   layered images. The base image can be explicitly provided, or discovered
   from `Containerfile`.


# Implementation details and notes

Getting package information from the container is tricky, and went through a
few iterations:

## Iteration 1
Let’s run the solver directly in the base image. This has a few cons though:

* Solving for non-native architectures requires emulation.
* It only works if the solver is using DNF 4 and the container provides dnf.
  Once the solver uses DNF 5, or for any base image using microdnf (or yum, or
  zypper…), it doesn’t work.
  * That can not be solved by installing the solver library into the image, as
    it would affect the results and where would it be installed from anyway?
    Using a statically linked depsolver would avoid installing dependencies,
    but then you need a different binary for each architecture.

## Iteration 2

Let’s run the solver on the host system, but filter out base image packages
after solving. Listing installed packages in the container is fairly easy, and
we can rely on rpm executable being present if the user wants to install
packages.

This approach doesn’t really work though.

* If the configured repos do not contain the full set of transitive
  dependencies, the solver will fail (or at least not see the full list of
  transitive dependencies, which can hide issues).
* If the configured repos contain newer versions of the packages already
  present on the image, the solver will include them in the result, and it
  becomes impossible to tell if the older version is sufficient or not.
  * If we keep the updated version in the result but the older version is fine,
    then we are prefetching something that will not be used.
  * If we remove the package but it is actually needed, the build process will
    fail.

## Iteration 3

We need to have information about the base image contents at the time the
transaction is being resolved. Let’s not even consider listing package details
with some incantation of `rpm -qa --queryformat`.

We can copy the rpmdb from the base image into some temporary directory and use
that as installroot during solving. A cleaner way might be to do `rpmdb
--exportdb` from the container and `rpmdb --importdb –root` into the temporary
location.

So the last thing is what the tool actually implements. It will pull the image,
run it and copy the rpmdb out to a temporary location on the host system. This
location is used as installroot for calling DNF.

It seems to work with any architecture, though it can result in pulling quite a
few images locally.

The main issue with this approach is that it's complicated to execute in
containers. To be able to run `podman run` inside a container, the parent
container has to be running with `--privileged` option. This may be a problem
for running in CI.

An alternative would be to pull the image, mount it, and copy the rpmdb out
using host tools. This way there could even be a cache for different images,
avoiding some pulls. I didn't experiment with this too much. The main hurdle
was turning the base image specification from Containerfile into an image id to
pull. I got stuck on resolving short names, but maybe it's not necessary?

## Iteration 4

This is a minor improvement over iteration 3. The `podman` usage can be
replaced by using `skopeo`, which can obtain the data witout requiring any
additional permissions when running inside containers.
