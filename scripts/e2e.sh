#!/usr/bin/env bash
# Run the e2e suite against the staging guild. Reads secrets from $E2E_ENV_FILE (never committed).
set -euo pipefail
: "${E2E_ENV_FILE:=/mnt/user/appdata/tower-bot/e2e.env}"
set -a
# Path is a runtime env var, not a constant shellcheck can follow.
# shellcheck disable=SC1090
. "$E2E_ENV_FILE"
set +a
exec pytest -m e2e tests/e2e -v
