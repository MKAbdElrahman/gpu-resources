# Security

## Reporting

If you find a security issue in this repository, open a private report via GitHub Security Advisories on the repo, or contact the maintainer.

## Operational notes

- Never commit `~/.docker/config.json`, dstack tokens, or cloud credentials.
- Run summaries under `--out` may include command strings; avoid embedding secrets in `--command` or `--env`.
- Registry passwords are redacted in `task.redacted.json`, but treat evidence directories as sensitive.
- Always verify `resource_release_verified` after cloud runs to avoid unexpected spend.
