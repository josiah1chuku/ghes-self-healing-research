"""
ml/collect_data.py
Pulls VM telemetry from Azure Log Analytics workspace.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

METRICS_QUERY = """
InsightsMetrics
| where TimeGenerated > ago({days}d)
| where Name in (
    "% Processor Time",
    "Available MBytes",
    "% Free Space",
    "Disk Read Bytes/sec",
    "Disk Write Bytes/sec",
    "Current Disk Queue Length",
    "Bytes Received/sec",
    "Bytes Sent/sec"
  )
| summarize avg_val = avg(Val) by bin(TimeGenerated, 1m), Name
| order by TimeGenerated asc
| project TimeGenerated, MetricName = Name, Value = avg_val
"""

ALERTS_QUERY = """
Alert
| where TimeGenerated > ago({days}d)
| project TimeGenerated, AlertName, Severity, State, Description
| order by TimeGenerated asc
"""

SYSLOG_QUERY = """
Syslog
| where TimeGenerated > ago({days}d)
| where SeverityLevel in ("err", "crit", "alert", "emerg")
| project TimeGenerated, Facility, SeverityLevel, SyslogMessage
| order by TimeGenerated asc
"""


def run_kql(workspace_id, query, output_file):
    print(f"  Querying: {output_file.name}...")
    cmd = [
        "az", "monitor", "log-analytics", "query",
        "--workspace", workspace_id,
        "--analytics-query", query,
        "--output", "json"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[:300]}")
        return pd.DataFrame()
    try:
        data = json.loads(result.stdout)
        df = pd.DataFrame(data)
        df.to_csv(output_file, index=False)
        print(f"  Saved {len(df)} rows to {output_file}")
        return df
    except Exception as e:
        print(f"  Parse error: {e}")
        return pd.DataFrame()


def pivot_metrics(metrics_df):
    if metrics_df.empty:
        return pd.DataFrame()
    metrics_df["TimeGenerated"] = pd.to_datetime(metrics_df["TimeGenerated"])
    metrics_df["MetricName"] = (
        metrics_df["MetricName"]
        .str.replace("%", "pct", regex=False)
        .str.replace("/", "_per_", regex=False)
        .str.replace(" ", "_", regex=False)
        .str.lower()
    )
    pivoted = metrics_df.pivot_table(
        index="TimeGenerated",
        columns="MetricName",
        values="Value",
        aggfunc="mean"
    ).reset_index()
    pivoted.columns.name = None
    pivoted = pivoted.sort_values("TimeGenerated")
    pivoted = pivoted.ffill().bfill()
    return pivoted


def label_anomalies(feature_df, alerts_df, window_minutes=10):
    feature_df = feature_df.copy()
    feature_df["anomaly_label"] = 0
    feature_df["anomaly_type"]  = "normal"
    if alerts_df.empty:
        print("  No alerts found - all labels normal.")
        return feature_df
    alerts_df["TimeGenerated"] = pd.to_datetime(alerts_df["TimeGenerated"])
    for _, alert in alerts_df.iterrows():
        alert_time   = alert["TimeGenerated"]
        window_start = alert_time - timedelta(minutes=window_minutes)
        mask = (
            (feature_df["TimeGenerated"] >= window_start) &
            (feature_df["TimeGenerated"] <= alert_time)
        )
        feature_df.loc[mask, "anomaly_label"] = 1
        alert_name = str(alert.get("AlertName", "")).lower()
        if "cpu" in alert_name or "memory" in alert_name or "disk" in alert_name:
            feature_df.loc[mask, "anomaly_type"] = "resource_exhaustion"
        elif "network" in alert_name or "nic" in alert_name:
            feature_df.loc[mask, "anomaly_type"] = "network_misconfiguration"
        else:
            feature_df.loc[mask, "anomaly_type"] = "config_drift"
    n = feature_df["anomaly_label"].sum()
    print(f"  Labeled {n}/{len(feature_df)} observations as anomalies")
    return feature_df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--env", default="dev",
                        choices=["dev", "test", "prod"])
    parser.add_argument("--label-anomalies", action="store_true")
    args = parser.parse_args()

    print(f"\n{'='*50}")
    print(f"  GHES ML Data Collection — {args.env}")
    print(f"{'='*50}\n")

    raw_metrics  = DATA_DIR / f"{args.env}_raw_metrics.csv"
    raw_alerts   = DATA_DIR / f"{args.env}_raw_alerts.csv"
    raw_syslog   = DATA_DIR / f"{args.env}_raw_syslog.csv"
    features_out = DATA_DIR / f"{args.env}_features.csv"

    print("Step 1: Collecting metrics...")
    metrics_df = run_kql(args.workspace_id,
                         METRICS_QUERY.format(days=args.days), raw_metrics)

    print("\nStep 2: Collecting alerts...")
    alerts_df = run_kql(args.workspace_id,
                        ALERTS_QUERY.format(days=args.days), raw_alerts)

    print("\nStep 3: Collecting syslog...")
    run_kql(args.workspace_id,
            SYSLOG_QUERY.format(days=args.days), raw_syslog)

    if metrics_df.empty:
        print("\nNo data. Check VM is running and AMA extension is installed.")
        sys.exit(1)

    print("\nStep 4: Building feature matrix...")
    feature_df = pivot_metrics(metrics_df)
    print(f"  Shape: {feature_df.shape}")

    if args.label_anomalies:
        print("\nStep 5: Labeling anomalies...")
        feature_df = label_anomalies(feature_df, alerts_df)

    feature_df.to_csv(features_out, index=False)
    print(f"\nSaved: {features_out}")
    print(f"Next: python ml/train.py --env {args.env}")


if __name__ == "__main__":
    main()
