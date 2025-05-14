# Changelog

## Unreleased

### Fixed

- Fixed a possible race condition in caching rpmdb from an image if two
  processes tried to use the same image at the same time.


## [0.15.0] - 2025-04-08

### Added

- Added `upgradePackages` directive for the configuration file. This allows to
  update packages in the base image.


## [0.14.0] - 2025-03-21

### Added

- Added new configuration key to enable or disable installation of weak
  dependencies.


## [0.13.2] - 2025-01-15

### Added

- The `$releasever` variable is now defined if it can be discovered from the
  base image.

### Fixed

- The tool will no longer try to inspect `scratch` "base image".


## [0.13.1] - 2024-12-05

### Fixed

- Fixed formatting of log message about storage being full.


## [0.13.0] - 2024-12-02

### Fixed

- RPMDB can now be extracted from flatpak runtime images, which uses
  `files/lib` for it.


## [0.12.0] - 2024-12-02

### Added

- Inclusion of source packages in the final manifest can now be disabled with a
  toplevel config option `noSources: true`. This is mostly useful for cases
  where there are no source repos available to silence the many warnings.

### Changed

- Only errors are now printed to stderr. Everything else goes to stdout.


## [0.11.0] - 2024-11-20

### Added

- A warning is printed when the base image specification doesn't include a
  registry. Not specifying a registry can cause confusing errors if skopeo
  (used by this tool) uses a different default than the build process later on.

- A separate executable is added which caches the result. This is only useful
  if for whatever reason the code has to be regenerated repeatedly with
  identical results.


## [0.10.0] - 2024-11-19

### Added

- Caching for extracted rpm databases has been added. This avoids the need to
  repeatedly download the same image if resolving runs multiple times on the
  same base image. The cached data is stored in
  `~/.cache/rpm-lockfile-prototype/rpmdbs`, and there is no automatic cleanup.

  When the filesystem with the cache gets over 80 % full, no new data will be
  cached anymore.

### Changed

- Repositories can now use `mirrorlist` or `metalink` instead of just `baseurl`.

### Fixed

- Variable substition in repoid is now supported. Only `arch` and `basearch`
  variables really make sense though, as anything else is taken from the host
  system where the tool is running.

- `$basearch` is now set correctly in cases it's not equal to `$arch`.


## [0.9.0] - 2024-09-19

### Added

- Module streams can be explicitly enabled or disabled by adding
  `moduleEnable`/`moduleDisable` directive to the input file.

- The `allowerasing` option can be specified at top level of the input file.

### Changed

- The list of packages in input file can now specify groups or modules (e.g.
  `@core` or `@container-tools:rhel8`; don't forget quotes for the yaml file to
  be valid). Anything accepted by `dnf install` should be acceptable in there.
  The `reinstallPackages` section still needs RPM names.


## [0.8.1] - 2024-09-06

### Changed

- The input file can now specify packages as objects, which can be used to add
  the package only to a subset of architectures.


## [0.8.0] - 2024-09-05

### Changed

- When a modular package is included in the resolved set, the lockfile will now
  contain references to modulemd metadata files from respective repos. These
  can then be prefetched together with the RPMs and included in the build
  repository to make dnf happy.


## [0.7.2] - 2024-09-03

### Fixed

- Obtaining labels from base image now works even if the image is specified
  using both tag and digest. The tag is ignored in such case.


## [0.7.1] - 2024-09-02

### Added

- The repos specified directly in the input file can now interpolate base image
  labels too. The specification for which image to use is the same as for the
  repofiles origin.

  This only works for `baseurl`.

- Where input configuration file specifies an image by pointing to Container
  file, it is now possible to provide an object with additional info on which
  stage to use. The stage can be specified by order (first, second, etc.), by
  name, or by a pattern that must be found in the image name.


## [0.7.0] - 2024-08-05

### Added

- The repofiles can now be specified by an object with `location` key (with the
  same meaning as the original single string specification). If the object
  additionally specifies `varsFromImage` or `varsFromContainerfile`, the
  resolver will query the image labels and interpolate them into the URL.

  This way it is possible to get to the exact same repos that were used to
  build the base image, but user must know where the raw repofile is.

- Alternatively, the repofiles can also be specified as a reference to a git
  repository, from which the actual repofile will be obtained. The keys in this
  case are `giturl`, `gitref` and `file`. Their meanings are in order: clone
  URL for the repository, commit sha and path inside the repo. All of the
  values can reference image labels if `varsFromImage` or
  `varsFromContainerfile` is specified using `{label-name}` syntax. The repo
  url can also reference environment variables using shell syntax (`$VAR` or
  `${VAR}`).

### Changed

- When no Containerfile is specified, stop assuming `Containerfile` and instead
  inspect current working directory to find either `Containerfile` or
  `Dockerfile`. If both exist, `Containerfile` will be preferred.


## [0.6.1] - 2024-07-31

### Fixed

- Follow up patch to prevous fix to correctly handle images that provide rpmdb
  in multiple locations via symlink.

## [0.6.0] - 2024-07-31

### Fixed

- Detection of installed packages in container images did not work correctly if
  the rpmdb in the image differed from local system. This is now fixed.

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
