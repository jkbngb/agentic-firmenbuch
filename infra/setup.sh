#!/usr/bin/env bash
# One-command, idempotent environment bring-up (§4.0).
# Bicep is declarative, so re-running creates only what's missing and leaves
# existing resources untouched. EU-only region with ordered fallback.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT="${ENVIRONMENT:-prod}"
BASE_NAME="${BASE_NAME:-firmenbuch}"
DEPLOYMENT_NAME="firmenbuch-$(date -u +%Y%m%d%H%M%S)"

# EU-only, ordered fallback (§4.0). Override with REGION to pin one.
REGIONS=("germanywestcentral" "westeurope" "northeurope")
if [[ -n "${REGION:-}" ]]; then
  REGIONS=("${REGION}")
fi

echo "==> Verifying Azure login"
if ! az account show >/dev/null 2>&1; then
  echo "ERROR: not logged in. Run 'az login' and select the subscription first." >&2
  exit 1
fi
SUB_ID="$(az account show --query id -o tsv)"
echo "    subscription: ${SUB_ID}"

echo "==> Validating the template (what-if) before deploying"
deploy() {
  local region="$1" mode="$2"
  az deployment sub "${mode}" \
    --name "${DEPLOYMENT_NAME}" \
    --location "${region}" \
    --template-file "${SCRIPT_DIR}/main.bicep" \
    --parameters location="${region}" environmentName="${ENVIRONMENT}" baseName="${BASE_NAME}"
}

for region in "${REGIONS[@]}"; do
  echo "==> Trying region: ${region}"
  # what-if first so a re-run clearly shows "no changes" when everything exists.
  if ! deploy "${region}" "what-if"; then
    echo "    what-if failed in ${region}, trying next region" >&2
    continue
  fi
  echo "==> Deploying to ${region} (idempotent)"
  if deploy "${region}" "create"; then
    echo "==> Done. Outputs:"
    az deployment sub show --name "${DEPLOYMENT_NAME}" --query properties.outputs -o jsonc
    echo ""
    echo "Next: push the FIRMENBUCH_API_KEY into Key Vault and build/push images to ACR."
    exit 0
  fi
  echo "    deploy failed in ${region}, trying next region" >&2
done

echo "ERROR: deployment failed in all EU regions: ${REGIONS[*]}" >&2
exit 1
