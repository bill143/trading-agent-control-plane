# Repository settings Bill applies (build step 0.01)

These settings need the repository owner's GitHub account; the builder cannot change them.

## 1. Make the repository private
GitHub → `bill143/trading-agent-control-plane` → **Settings** → **General** → bottom of page,
**Danger Zone** → **Change repository visibility** → **Make private**.

## 2. Protect `main`
**Settings** → **Rules** → **Rulesets** → **New ruleset** → **Import a ruleset** → choose
`docs/github/main-ruleset.json` from this repository → **Create**.

What the ruleset enforces on `main`:
- No direct pushes: every change arrives through a pull request.
- The CI checks must pass before merging (lint/types/tests on Windows and Ubuntu, the Windows
  setup script, the dependency vulnerability scan and the full-history secret scan).
- The branch is up to date with `main` before merging.
- No force-pushes and no deletion of `main`.

## 3. Turn on GitHub secret scanning
**Settings** → **Code security** → enable **Secret Protection** / **Push protection** (available
on private repositories with GitHub Advanced Security; on public repositories it is free).
