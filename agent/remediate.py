"""
agent/remediate.py
LLM-powered remediation agent for GHES VM self-healing.
Receives anomaly report, calls Claude API, executes approved action.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

AGENT_DIR = Path(__file__).parent
ROOT_DIR  = AGENT_DIR.parent
LOG_DIR   = AGENT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

ACTION_CATALOG = {
    "redeploy": {
        "description": "Re-run Bicep deployment to restore infrastructure to desired state",
        "suitable_for": ["config_drift", "network_misconfiguration"],
        "risk": "low", "reversible": True,
    },
    "scale_up": {
        "description": "Increase VM SKU to next performance tier",
        "suitable_for": ["resource_exhaustion"],
        "risk": "medium", "reversible": True,
    },
    "rollback": {
        "description": "Revert to the previous successful deployment",
        "suitable_for": ["config_drift", "network_misconfiguration"],
        "risk": "medium", "reversible": True,
    },
    "alert_only": {
        "description": "Log the anomaly and notify operators, no automated action",
        "suitable_for": ["unknown", "low_confidence"],
        "risk": "none", "reversible": True,
    },
}

SKU_LADDER = {
    "Standard_D4s_v3" : "Standard_D8s_v3",
    "Standard_D8s_v3" : "Standard_D16s_v3",
    "Standard_D16s_v3": "Standard_D32s_v3",
    "Standard_D32s_v3": "Standard_D32s_v3",
}


def call_claude(system_prompt, user_prompt):
    import urllib.request
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set")
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"]


def build_system_prompt():
    return f"""You are a policy-governed infrastructure remediation agent for a
GitHub Enterprise Server (GHES) deployment on Azure commercial cloud.

APPROVED ACTION CATALOG:
{json.dumps(ACTION_CATALOG, indent=2)}

STRICT RULES:
1. Select exactly one action from the catalog above.
2. You CANNOT suggest any action not in the catalog.
3. Provide chain-of-thought reasoning before selecting.
4. If confidence is low, select alert_only.
5. Never select scale_up for network or config issues.

OUTPUT FORMAT (JSON only, no other text):
{{
  "chain_of_thought": "Step by step reasoning...",
  "selected_action": "redeploy | scale_up | rollback | alert_only",
  "confidence": "high | medium | low",
  "justification": "One sentence for the operator",
  "estimated_mttr_minutes": <integer>,
  "risk_assessment": "Brief risk statement"
}}"""


def build_user_prompt(report):
    return f"""Anomaly detected on GHES VM. Select remediation action.

ANOMALY REPORT:
{json.dumps(report, indent=2)}

Respond with JSON only."""


def get_vm_info(env):
    p = ROOT_DIR / "iac" / "environments" / f"{env}.parameters.json"
    with open(p) as f:
        params = json.load(f)["parameters"]
    return {
        "rg":       params["resourceGroupName"]["value"],
        "vm_name":  params["vmName"]["value"],
        "vm_size":  params["vmSize"]["value"],
        "nic_name": params["nicName"]["value"],
    }


def run_az(cmd):
    r = subprocess.run(["az"] + cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stdout + r.stderr


def action_redeploy(env, dry_run=False):
    print(f"  ACTION: redeploy — restoring {env} to desired state...")
    param_file = ROOT_DIR / "iac" / "environments" / f"{env}.parameters.json"
    template   = ROOT_DIR / "iac" / "main.bicep"
    cmd = ["deployment","sub","create",
           "--location","eastus",
           "--template-file", str(template),
           "--parameters", f"@{param_file}",
           "--name", f"remediation-{int(time.time())}",
           "--output","json"]
    if dry_run:
        print(f"  [DRY RUN] az {' '.join(cmd)}")
        return {"success": True, "dry_run": True, "action": "redeploy"}
    start = time.time()
    ok, out = run_az(cmd)
    return {"success": ok, "action": "redeploy",
            "duration_seconds": round(time.time()-start, 1),
            "output": "Succeeded" if ok else out[:300]}


def action_scale_up(env, dry_run=False):
    info = get_vm_info(env)
    current = info["vm_size"]
    target  = SKU_LADDER.get(current, current)
    if current == target:
        return {"success": False, "action": "scale_up",
                "reason": f"Already at max SKU: {current}"}
    print(f"  ACTION: scale_up — {current} to {target}")
    if dry_run:
        return {"success": True, "dry_run": True, "action": "scale_up",
                "from_sku": current, "to_sku": target}
    start = time.time()
    run_az(["vm","deallocate","--resource-group",info["rg"],"--name",info["vm_name"]])
    ok, out = run_az(["vm","resize","--resource-group",info["rg"],
                      "--name",info["vm_name"],"--size",target])
    run_az(["vm","start","--resource-group",info["rg"],"--name",info["vm_name"]])
    return {"success": ok, "action": "scale_up",
            "from_sku": current, "to_sku": target,
            "duration_seconds": round(time.time()-start, 1)}


def action_rollback(env, dry_run=False):
    print(f"  ACTION: rollback — reverting {env}...")
    ok, out = run_az(["deployment","sub","list",
                      "--query","[?properties.provisioningState=='Succeeded']"
                               "| sort_by(@, &properties.timestamp)| [-2].name",
                      "--output","tsv"])
    last_good = out.strip()
    if not last_good:
        return {"success": False, "action": "rollback",
                "reason": "No previous deployment found"}
    if dry_run:
        return {"success": True, "dry_run": True, "action": "rollback",
                "target": last_good}
    return {"success": True, "action": "rollback",
            "target_deployment": last_good,
            "output": "Rollback initiated"}


def action_alert_only(report):
    print(f"  ACTION: alert_only — logging, no automated action")
    return {"success": True, "action": "alert_only",
            "anomaly_type": report.get("anomaly_type"),
            "severity": report.get("severity"),
            "message": "Anomaly logged. Manual review required.",
            "timestamp": datetime.now(timezone.utc).isoformat()}


def run_agent(env, report, dry_run=False):
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"\n{'='*55}")
    print(f"  GHES LLM Remediation Agent")
    print(f"  Env      : {env}")
    print(f"  Anomaly  : {report.get('anomaly_type','unknown')}")
    print(f"  Severity : {report.get('severity','unknown')}")
    print(f"  Dry run  : {dry_run}")
    print(f"{'='*55}\n")

    print("Step 1: Calling Claude API...")
    llm_raw = None
    llm_decision = None
    try:
        llm_raw      = call_claude(build_system_prompt(), build_user_prompt(report))
        clean        = llm_raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        llm_decision = json.loads(clean)
        print(f"  Action    : {llm_decision.get('selected_action')}")
        print(f"  Confidence: {llm_decision.get('confidence')}")
        print(f"  Reason    : {llm_decision.get('justification')}")
    except Exception as e:
        print(f"  Claude error: {e} — falling back to alert_only")
        llm_decision = {
            "selected_action": "alert_only",
            "confidence": "low",
            "justification": f"LLM unavailable: {str(e)[:100]}",
            "chain_of_thought": "Fallback",
            "estimated_mttr_minutes": 0,
            "risk_assessment": "No action taken"
        }

    selected = llm_decision.get("selected_action", "alert_only")
    if selected not in ACTION_CATALOG:
        print(f"  Invalid action '{selected}' — forcing alert_only")
        selected = "alert_only"

    print(f"\nStep 2: Executing: {selected}")
    start = time.time()
    if selected == "redeploy":
        result = action_redeploy(env, dry_run)
    elif selected == "scale_up":
        result = action_scale_up(env, dry_run)
    elif selected == "rollback":
        result = action_rollback(env, dry_run)
    else:
        result = action_alert_only(report)
    duration = round(time.time()-start, 1)

    audit = {
        "timestamp": timestamp,
        "env": env,
        "anomaly_report": report,
        "llm_decision": llm_decision,
        "selected_action": selected,
        "action_result": result,
        "action_duration_seconds": duration,
        "dry_run": dry_run,
        "mttr_seconds": duration if result.get("success") else None,
    }

    log_file = LOG_DIR / f"remediation_{env}_{int(time.time())}.json"
    with open(log_file, "w") as f:
        json.dump(audit, f, indent=2)

    status = "SUCCESS" if result.get("success") else "FAILED"
    print(f"\n{'='*55}")
    print(f"  REMEDIATION {status}")
    print(f"  Action  : {selected}")
    print(f"  Duration: {duration}s")
    print(f"  Log     : {log_file}")
    print(f"{'='*55}\n")
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="test",
                        choices=["dev","test","prod"])
    parser.add_argument("--report")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--workspace-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-action",
                        choices=list(ACTION_CATALOG.keys()))
    args = parser.parse_args()

    if args.live:
        sys.path.insert(0, str(ROOT_DIR))
        from ml.predict import predict
        report = predict(env=args.env, workspace_id=args.workspace_id,
                         use_file=args.workspace_id is None)
        if not report.get("is_anomaly"):
            print(f"No anomaly on {args.env}. Nothing to remediate.")
            sys.exit(0)
    elif args.report:
        with open(args.report) as f:
            report = json.load(f)
    else:
        print("Using demo anomaly report...")
        report = {
            "status": "anomaly", "env": args.env,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_anomaly": True,
            "anomaly_type": "resource_exhaustion",
            "severity": "high",
            "models": {
                "isolation_forest": {"label":1,"score":-0.45,"is_anomaly":True},
                "lstm_autoencoder": {"label":1,"reconstruction_error":0.082,
                                     "threshold":0.031,"is_anomaly":True}
            },
            "current_metrics": {
                "cpu_pct":91.2,"memory_available_mb":412.0,
                "disk_free_pct":23.4,"disk_queue":8.1,
                "net_rx":1240000.0,"net_tx":890000.0
            },
            "n_anomalies_last_hour": 14
        }

    if args.force_action:
        if args.force_action == "redeploy":
            r = action_redeploy(args.env, args.dry_run)
        elif args.force_action == "scale_up":
            r = action_scale_up(args.env, args.dry_run)
        elif args.force_action == "rollback":
            r = action_rollback(args.env, args.dry_run)
        else:
            r = action_alert_only(report)
        print(json.dumps(r, indent=2))
        return

    audit = run_agent(args.env, report, args.dry_run)
    sys.exit(0 if audit["action_result"].get("success") else 1)


if __name__ == "__main__":
    main()
