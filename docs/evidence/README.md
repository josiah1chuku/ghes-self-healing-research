# Deployment evidence (dev environment, 2026-10-13)

- validation-dev.txt: output of validate-claims.sh. The 9 resource checks and the two template-rejection tests pass.
  The N3 line shows FAIL because the check crashed locally (Azure CLI Python import error), not because the policy failed.
- probe-result.txt: manual re-run of N3 as a Bicep deployment. Azure Policy denied it with RequestDisallowedByPolicy
  (assignment deny-nic-public-ip, definition "Network interfaces should not have public IPs").
- VM: GitHub Enterprise Server 3.22.2 image, Standard_D8s_v4, East US. Trusted Launch is not supported by this image.
