# Secrets policy (build step 0.06)

Applies to every person and every AI process working on this repository.

## Rules
1. **No key, password, token or account number is ever written into code, config, tests, docs,
   commit messages or logs.** This includes Databento keys, Interactive Brokers / Rithmic /
   Tradovate logins, language-AI provider keys, phone-push credentials and two-factor seeds.
2. **Where secrets live:** only in the encrypted vault (build step 1.18): Windows protected
   storage (DPAPI / Credential Manager) on the PC, a cloud secret manager on the server.
3. **`.env` files are never committed.** `.gitignore` excludes `.env` and `.env.*`; only
   `.env.example` (non-secret defaults) is tracked. A pre-commit hook refuses any `.env` file.
4. **Every commit is scanned** by gitleaks (pre-commit hook, `.gitleaks.toml`: the full default
   rule set plus Databento, Vercel and broker-credential rules). CI rescans the full git history
   on every push and fails the build on any finding.
5. **Logs are redacted.** The engine's logging configuration (steps 1.18 / 1.19) masks every
   value read from the vault before a record is written. A test asserts that a known test secret
   never appears in any log output.
6. **Never paste secrets into chat**, issues, pull requests or screenshots. Keys are entered only
   in the app's vault screen.

## If a secret is exposed
1. Revoke / rotate it at the provider immediately (before anything else).
2. Remove it from the working tree and history; record the incident (no secret values) in
   `DECISIONS.md`.
3. Re-run `gitleaks git --config .gitleaks.toml .` and confirm a clean result.

## Local setup
`scripts/setup-windows.ps1` installs the hooks. Manually: `uv run pre-commit install`.
