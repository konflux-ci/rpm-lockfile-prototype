---
name: release
description: Create a new release for rpm-lockfile-prototype. Use when the user asks to make a release, bump the version, or tag a new version.
---

# Release Skill

Create a new release for rpm-lockfile-prototype.

## Process

### 1. Determine the new version

- Look at the current version in `pyproject.toml`.
- Look at git tags to see the version history: `git tag --sort=-creatordate`.
- The project uses semantic versioning. Bump minor version for new features,
  patch for bug fixes only.

### 2. Check for an existing Unreleased section

- Read the top of `CHANGELOG.md` and check if there is already an
  `## [Unreleased]` or `## Unreleased` section. Changes are sometimes added
  there as they are merged, before a release is made.
- If an Unreleased section exists, use its content as the starting point for
  the changelog entry. It may already contain all the needed text, or it may
  need additions from commits not yet documented.

### 3. Identify changes since the last release

- Find the latest tag and list commits since then:
  `git log <latest-tag>..HEAD --oneline`
- Categorize changes as Added, Changed, or Fixed per the Keep a Changelog
  convention.
- Ignore CI, infrastructure, and internal-only changes that are not visible to
  users. Only include things that affect how users interact with the tool.
- If a fix is part of a new feature that did not exist in the previous release,
  fold it into the feature description rather than listing it separately as a
  fix.
- Cross-reference with the Unreleased section (if one existed) to avoid
  duplicating entries or missing commits that were not documented there.

### 4. Write the changelog entry

- Draft the new entry for `CHANGELOG.md` following the style of existing
  entries.
- **Present the draft to the user and ask for explicit approval before making
  any file changes.** Use the Question tool to ask for approval, offering
  options to approve, edit, or reject the draft.

### 5. Update files

Only after the user approves the changelog text, update these three files:

- `CHANGELOG.md` -- replace the `## [Unreleased]` / `## Unreleased` header
  (if present) with `## [X.Y.Z] - YYYY-MM-DD`, or add a new section at the
  top (after the `# Changelog` heading) if there was no Unreleased section.
  Use today's date.
- `pyproject.toml` -- update the `version` field.
- `README.md` -- update the version tag in the pip install URL
  (search for `archive/refs/tags/v`).

### 6. Commit and tag

- Stage only the three changed files.
- Commit with message: `Bump version to X.Y.Z`
- Create a lightweight tag: `git tag vX.Y.Z`
- Do NOT push. Tell the user to push the commit and tag:
  `git push origin main vX.Y.Z`
- Mention that the GitHub Actions workflow in `.github/workflows/release.yml`
  will automatically create a GitHub release from the tag.
