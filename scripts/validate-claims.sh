#!/usr/bin/env bash
# Verify the paper's IaC claims on a deployed environment.
# Usage: scripts/validate-claims.sh dev
# Optional env: PARAM_FILE (rendered parameter file), N3_ATTEMPTS (retries while Azure Policy propagates, 60 s apart).
set -uo pipefail
ENV=${1:-dev}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PF=${PARAM_FILE:-$ROOT/iac/environments/$ENV.parameters.json}
ATTEMPTS=${N3_ATTEMPTS:-1}
RG=ghes-research-$ENV-rg; VM=ghes-research-$ENV-vm; NIC=ghes-research-$ENV-nic
failures=0
pass(){ printf 'PASS  %s\n' "$1"; }
fail(){ printf 'FAIL  %s\n' "$1"; failures=$((failures+1)); }
chk(){ [[ "$2" == "$3" ]] && pass "$1 ($2)" || fail "$1 (got '$2', want '$3')"; }

chk "C1 no public IP on NIC"       "$(az network nic show -g $RG -n $NIC --query 'ipConfigurations[0].publicIPAddress' -o tsv)" ""
chk "C2 static private IP"         "$(az network nic show -g $RG -n $NIC --query 'ipConfigurations[0].privateIPAllocationMethod' -o tsv)" "Static"
chk "C4 system-assigned identity"  "$(az vm show -g $RG -n $VM --query identity.type -o tsv)" "SystemAssigned"
chk "C5 boot diagnostics"          "$(az vm show -g $RG -n $VM --query diagnosticsProfile.bootDiagnostics.enabled -o tsv)" "true"
chk "C6 encryption at host"        "$(az vm show -g $RG -n $VM --query securityProfile.encryptionAtHost -o tsv)" "true"
chk "C6 OS disk CMK"               "$(az disk show -g $RG -n $VM-os-disk --query encryption.type -o tsv)" "EncryptionAtRestWithCustomerKey"
chk "C6 data disk CMK"             "$(az disk show -g $RG -n $VM-data-disk --query encryption.type -o tsv)" "EncryptionAtRestWithCustomerKey"
chk "C7 password auth disabled"    "$(az vm show -g $RG -n $VM --query osProfile.linuxConfiguration.disablePasswordAuthentication -o tsv)" "true"
chk "C7 classification tag"        "$(az vm show -g $RG -n $VM --query 'tags.Environment' -o tsv)" "$ENV"

echo; echo "== Negative tests: these deployments MUST be rejected =="
az deployment sub validate -l eastus -f "$ROOT/iac/main.bicep" -p @"$PF" -p enablePublicIp=true >/dev/null 2>&1 \
  && fail "N1 enablePublicIp=true was accepted" || pass "N1 enablePublicIp=true rejected by @allowed"
az deployment sub validate -l eastus -f "$ROOT/iac/main.bicep" -p @"$PF" -p enableCmkEncryption=false >/dev/null 2>&1 \
  && fail "N2 enableCmkEncryption=false was accepted" || pass "N2 enableCmkEncryption=false rejected by @allowed"

SUBNET=$(az network nic show -g $RG -n $NIC --query 'ipConfigurations[0].subnet.id' -o tsv)
n3=fail
for i in $(seq 1 "$ATTEMPTS"); do
  out=$(az deployment group create -g $RG -f "$ROOT/policy/probe-public-ip.bicep" -p subnetId="$SUBNET" -o none 2>&1)
  if echo "$out" | grep -q RequestDisallowedByPolicy; then n3=pass; break; fi
  # The probe got through (policy not enforcing yet): remove it and retry
  az network nic delete -g $RG -n probe2-nic >/dev/null 2>&1
  az network public-ip delete -g $RG -n probe2-pip >/dev/null 2>&1
  [ "$i" -lt "$ATTEMPTS" ] && { echo "N3: policy not enforcing yet, retry $i/$ATTEMPTS in 60 s"; sleep 60; }
done
az network public-ip delete -g $RG -n probe2-pip >/dev/null 2>&1 || true
[ $n3 = pass ] && pass "N3 out-of-band public IP denied by Azure Policy (RequestDisallowedByPolicy)" \
               || fail "N3 policy did not block out-of-band public IP"

echo; echo "Failures: $failures"
exit $((failures>0))
