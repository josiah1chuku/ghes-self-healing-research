#!/usr/bin/env bash
# Step 0 - one-time subscription prerequisites and pre-flight checks. Run in Cloud Shell or any az-logged-in shell.
set -euo pipefail
az account show --query "{sub:id,name:name}" -o tsv

# Providers and the host-encryption feature that securityProfile.encryptionAtHost needs
for ns in Microsoft.Compute Microsoft.Network Microsoft.KeyVault Microsoft.OperationalInsights Microsoft.Insights Microsoft.PolicyInsights; do
  az provider register --namespace "$ns" --wait
done
az feature register --namespace Microsoft.Compute --name EncryptionAtHost
az provider register --namespace Microsoft.Compute

echo "== GHES images (pick a supported, current version and pin it in the parameter files) =="
az vm image list --all --publisher GitHub --offer GitHub-Enterprise -o table | tail -15

echo "== Generation check: Trusted Launch needs a Gen2 image. Expect V2 =="
az vm image show --urn GitHub:GitHub-Enterprise:github-enterprise-gen2:latest --query "{gen:hyperVGeneration, plan:plan}" -o json || true

echo "== Accept marketplace terms (required once per subscription) =="
# GHES image has no purchase plan (az vm image show returns plan: null), so no terms step is needed.

echo "== Quota: need 16 vCPU of DSv3 for prod D16s_v3 =="
az vm list-usage -l eastus --query "[?contains(name.value,'standardDSv3Family')]" -o table
echo "Feature state (wait for Registered):"; az feature show --namespace Microsoft.Compute --name EncryptionAtHost --query properties.state -o tsv
