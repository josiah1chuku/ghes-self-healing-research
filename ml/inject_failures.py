"""
ml/inject_failures.py
Injects synthetic failures into GHES VM to generate labeled training data.
"""

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ML_DIR   = Path(__file__).parent
DATA_DIR = ML_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_FILE = DATA_DIR / "injection_log.jsonl"


def log_event(env, failure_class, phase, details=None):
    entry = {
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "env":           env,
        "failure_class": failure_class,
        "phase":         phase,
        "details":       details or {}
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"  [{phase.upper()}] {failure_class} @ {entry['timestamp']}")


def az(cmd, check=True):
    r = subprocess.run(["az"] + cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"  AZ ERROR: {r.stderr[:200]}")
    return r


def get_vm_info(env):
    p = Path(__file__).parent.parent / "iac" / "environments" / f"{env}.parameters.json"
    with open(p) as f:
        params = json.load(f)["parameters"]
    return {
        "rg":       params["resourceGroupName"]["value"],
        "vm_name":  params["vmName"]["value"],
        "nic_name": params["nicName"]["value"],
    }


def inject_resource_exhaustion(env, duration=300):
    info = get_vm_info(env)
    print(f"\n[INJECT] Resource Exhaustion on {info['vm_name']}")
    log_event(env, "resource_exhaustion", "start",
              {"duration": duration, "vm": info["vm_name"]})
    script = f"""
which stress-ng || sudo apt-get install -y stress-ng -q
sudo stress-ng --cpu $(nproc) --vm 2 --vm-bytes 80% --timeout {duration}s
"""
    r = az(["vm","run-command","invoke",
            "--resource-group", info["rg"],
            "--name", info["vm_name"],
            "--command-id","RunShellScript",
            "--scripts", script, "--output","json"])
    if r.returncode == 0:
        log_event(env, "resource_exhaustion", "end")
        print("  Waiting 2 min for metrics to settle...")
        time.sleep(120)
        log_event(env, "resource_exhaustion", "recovered")
    else:
        print("  Injection failed")


def inject_config_drift(env):
    info = get_vm_info(env)
    print(f"\n[INJECT] Config Drift on {info['nic_name']}")
    log_event(env, "config_drift", "start",
              {"nic": info["nic_name"], "change": "Static->Dynamic"})
    r = az(["network","nic","ip-config","update",
            "--resource-group", info["rg"],
            "--nic-name", info["nic_name"],
            "--name","ipconfig1",
            "--set","privateIPAllocationMethod=Dynamic"])
    if r.returncode == 0:
        print("  Drift injected. Waiting 5 min...")
        time.sleep(300)
        log_event(env, "config_drift", "end")
        print("  Remediating...")
        az(["network","nic","ip-config","update",
            "--resource-group", info["rg"],
            "--nic-name", info["nic_name"],
            "--name","ipconfig1",
            "--set","privateIPAllocationMethod=Static"])
        log_event(env, "config_drift", "recovered",
                  {"remediation": "Static IP restored"})


def inject_network_misconfig(env):
    info     = get_vm_info(env)
    nsg_name = f"{info['vm_name']}-nsg"
    print(f"\n[INJECT] Network Misconfig on {nsg_name}")
    log_event(env, "network_misconfig", "start",
              {"nsg": nsg_name, "change": "Block port 8080"})
    r = az(["network","nsg","rule","create",
            "--resource-group", info["rg"],
            "--nsg-name", nsg_name,
            "--name","RESEARCH-INJECT-DENY-8080",
            "--priority","100","--protocol","Tcp",
            "--destination-port-ranges","8080",
            "--access","Deny","--direction","Inbound"])
    if r.returncode == 0:
        print("  Port 8080 blocked. Waiting 5 min...")
        time.sleep(300)
        log_event(env, "network_misconfig", "end")
        print("  Remediating...")
        az(["network","nsg","rule","delete",
            "--resource-group", info["rg"],
            "--nsg-name", nsg_name,
            "--name","RESEARCH-INJECT-DENY-8080"])
        log_event(env, "network_misconfig", "recovered",
                  {"remediation": "NSG rule removed"})
    else:
        print(f"  NSG not found. Check: az network nsg list -g {info['rg']} -o table")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="test", choices=["dev","test"])
    parser.add_argument("--type",
                        choices=["resource_exhaustion","config_drift","network_misconfig"])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--stress-duration", type=int, default=300)
    args = parser.parse_args()

    print(f"\n{'='*50}")
    print(f"  GHES Failure Injection — {args.env}")
    print(f"  Log: {LOG_FILE}")
    print(f"{'='*50}")
    print("\n  WARNING: This will temporarily degrade your VM.")
    print("  Ctrl+C within 5 seconds to cancel...\n")
    time.sleep(5)

    types = (["resource_exhaustion","config_drift","network_misconfig"]
             if args.all else [args.type] if args.type else [])
    if not types:
        print("Specify --type or --all")
        return

    for run in range(args.runs):
        print(f"\nRun {run+1}/{args.runs}")
        for ft in types:
            if ft == "resource_exhaustion":
                inject_resource_exhaustion(args.env, args.stress_duration)
            elif ft == "config_drift":
                inject_config_drift(args.env)
            elif ft == "network_misconfig":
                inject_network_misconfig(args.env)
            if len(types) > 1:
                print("  Waiting 3 min between injections...")
                time.sleep(180)

    print(f"\nDone. Log: {LOG_FILE}")
    print(f"Next: python ml/collect_data.py --workspace-id <id> --env {args.env} --label-anomalies")


if __name__ == "__main__":
    main()
