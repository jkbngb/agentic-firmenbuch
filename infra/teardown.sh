#!/usr/bin/env bash
# Explicit, separate teardown (§4.0). Deletes the whole resource group.
set -euo pipefail

ENVIRONMENT="${ENVIRONMENT:-prod}"
BASE_NAME="${BASE_NAME:-firmenbuch}"
RG="${RESOURCE_GROUP:-rg-${BASE_NAME}-${ENVIRONMENT}}"

if ! az account show >/dev/null 2>&1; then
  echo "ERROR: not logged in. Run 'az login' first." >&2
  exit 1
fi

if ! az group show --name "${RG}" >/dev/null 2>&1; then
  echo "Resource group '${RG}' does not exist; nothing to tear down."
  exit 0
fi

read -r -p "Delete resource group '${RG}' and ALL its resources? [y/N] " confirm
if [[ "${confirm}" != "y" && "${confirm}" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

echo "==> Deleting resource group ${RG}"
az group delete --name "${RG}" --yes --no-wait
echo "Deletion started (running in the background)."
