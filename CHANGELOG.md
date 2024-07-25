# Changelog

## [0.5.1] - 2024-07-25

### Changed

- Allow `--flatpak` to be combined with `--bare` in config file.

## [0.5.0] - 2024-07-23

### Changed

- If Containerfile defines multiple stage, the last base image will be
  extracted.

## [0.4.0] - 2024-07-16

### Added

- Most command line options can now also be set in the input configuration file
  in the new `context` section.


## [0.3.0] - 2024-07-12

### Added

- Add `--flatpak` option to read packages from `container.yaml`. For Flatpak
  containers, the set of packages to include in the Flatpak is defined in the
  `container.yaml`, and for runtimes, can be very big, so we don't want to
  duplicate it in `rpms.in.yaml`. Instead read the package list from
  `container.yaml`.


## [0.2.0] - 2024-06-25

### Changed

- When image specification contains both a tag and a digest, remove the tag and
  ignore it. Skopeo fails when both are provided, and this new behaviour
  matches what podman 4.9.4 does. There's a message provided that the tag is
  being stripped out.

## [0.1.0] - 2024-06-21

### Added

- The input file can specify `reinstallPackages` as a list of strings. These
  are packages already installed in the base image that will be reinstalled.

  Listing something that is not in the base image will lead to an error.

  There will also be an error if the configured repos do not contain identical
  version to the package in the base image.

## [0.1.0-alpha.7] - 2024-06-20

### Added

- List of architectures to resolve on can now be specified in the input file.
  The list can be overridden by command line options. The precedence is 1.
  architectures specified on command line, 2. list from config file, 3. current
  host architecture. The first one provided wins.

## [0.1.0-alpha.6] - 2024-06-19

### Added

- New command line option `--allowerasing` makes it possible to remove packages
  from the base image to replace them with a conflicting one.


## [0.1.0-alpha.5] - 2024-06-10

### Changed

- There is no explicit dependency on DNF anymore. It was causing problems in
  certain installation scenarios. Instead, if you run the tool with no
  python3-dnf package available, an error message is printed with instructions
  on what to do.

## [0.1.0-alpha.4] - 2024-06-10

### Fixed

- Correctly process aarch64 images.

## [0.1.0-alpha.3] - 2024-06-10

### Added

- Any repository option can be specified in `contentOrigins.repos`. There is no
  schema validation on them though. The resolver only requires `repoid` and
  `baseurl`. Anything else will be forwarded to DNF. Handling unrecognized
  options depends on what DNF does. On Fedora 39 it silently ignores unknown
  options.

### Changed

- All options from repofiles are now honored and passed over to DNF. This means
  that DNF will now see even disabled repos, but will not include any packages
  from there.
- Extracting rpmdb from an image is now done with skopeo. This makes it
  possible to run inside non-privileged containers. The `--pull` option is now
  deprecated and doesn't do anything, the image is always pulled fresh.

## [0.1.0-alpha.2] - 2024-04-25

### Added

- `arch-include` directive in treefile is now handled

### Changed

- Missing sources are reported as a warning only.
- List of `packages` in input file can be empty.

### Fixed

- Compatibility with Python 3.9

## [0.1.0-alpha.1] - 2024-04-11

Initial release
