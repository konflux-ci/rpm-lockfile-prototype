%global project rpm-lockfile-prototype
Name:           python-%project
Version:        0.7.2
Release:        1
Summary:        Build manual page from python's ArgumentParser object.

License:        GPL-3.0-or-later
URL:            https://github.com/konflux-ci/rpm-lockfile-prototype
Source0:        https://github.com/konflux-ci/rpm-lockfile-prototype/archive/refs/tags/v%version.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-dnf

%global _description %{expand:
Generate RPM lockfile.}

%description %_description

%package -n python3-%project
Summary: RPM lockfile generator

%description -n python3-%project %_description


%prep
%autosetup -p1


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
# For official Fedora packages, including files with '*' +auto is not allowed
# Replace it with a list of relevant Python modules/globs and list extra files in %%files
%pyproject_save_files '*' +auto


%check
%pyproject_check_import


%files -n python3-%project
%license COPYING
%doc README.md
%_bindir/rpm-lockfile-prototype
%python3_sitelib/rpm_lockfile/*.py
%python3_sitelib/rpm_lockfile/__pycache__/*.pyc
%python3_sitelib/rpm_lockfile/content_origin/*.py
%python3_sitelib/rpm_lockfile/content_origin/__pycache__/*.pyc
%python3_sitelib/rpm_lockfile_prototype-*dist-info


%changelog
* Tue Sep 03 2024 Pavel Raiskup <praiskup@redhat.com> - 0.7.2-1
- initial RPM packaging
