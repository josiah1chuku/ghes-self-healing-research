"""
ml/predict.py
Real-time anomaly detection — feeds the LLM remediation agent.
"""

import argparse
import json
import pickle
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ML_DIR    = Path(__file__).parent
DATA_DIR  = ML_DIR / "data"
MODEL_DIR = ML_DIR / "models"
SEQUENCE_LENGTH = 60

FEATURE_COLS = [
    "pct_processor_time", "available_mbytes", "pct_free_space",
    "disk_read_bytes_per_sec", "disk_write_bytes_per_sec",
    "current_disk_queue_length", "bytes_received_per_sec", "bytes_sent_per_sec",
]


def load_models(env):
    for p in [MODEL_DIR / f"{env}_scaler.pkl",
              MODEL_DIR / f"{env}_isolation_forest.pkl",
              MODEL_DIR / f"{env}_metadata.json"]:
        if not p.exists():
            raise FileNotFoundError(f"Missing: {p}\nRun: python ml/train.py --env {env}")
    with open(MODEL_DIR / f"{env}_scaler.pkl", "rb") as f:
        scaler = pickle.load(f)
    with open(MODEL_DIR / f"{env}_isolation_forest.pkl", "rb") as f:
        if_model = pickle.load(f)
    with open(MODEL_DIR / f"{env}_metadata.json") as f:
        meta = json.load(f)
    lstm_model = None
    lstm_path  = MODEL_DIR / f"{env}_lstm_autoencoder.keras"
    if lstm_path.exists():
        try:
            import tensorflow as tf
            lstm_model = tf.keras.models.load_model(str(lstm_path))
        except Exception as e:
            print(f"  LSTM load warning: {e}")
    return if_model, lstm_model, scaler, meta


def fetch_live(workspace_id, minutes=10):
    query = f"""
InsightsMetrics
| where TimeGenerated > ago({minutes}m)
| where Name in ("% Processor Time","Available MBytes","% Free Space",
    "Disk Read Bytes/sec","Disk Write Bytes/sec","Current Disk Queue Length",
    "Bytes Received/sec","Bytes Sent/sec")
| summarize avg_val = avg(Val) by bin(TimeGenerated, 1m), Name
| order by TimeGenerated asc
| project TimeGenerated, MetricName = Name, Value = avg_val
"""
    cmd = ["az","monitor","log-analytics","query",
           "--workspace", workspace_id,
           "--analytics-query", query, "--output","json"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:300])
    df = pd.DataFrame(json.loads(r.stdout))
    if df.empty:
        return pd.DataFrame()
    df["TimeGenerated"] = pd.to_datetime(df["TimeGenerated"])
    df["MetricName"] = (df["MetricName"]
        .str.replace("%","pct",regex=False)
        .str.replace("/","_per_",regex=False)
        .str.replace(" ","_",regex=False)
        .str.lower())
    pivot = df.pivot_table(index="TimeGenerated",columns="MetricName",
                           values="Value",aggfunc="mean").reset_index()
    pivot.columns.name = None
    return pivot


def load_from_file(env):
    path = DATA_DIR / f"{env}_features.csv"
    if not path.exists():
        raise FileNotFoundError(f"No file: {path}")
    df = pd.read_csv(path, parse_dates=["TimeGenerated"])
    return df.sort_values("TimeGenerated").tail(SEQUENCE_LENGTH + 10)


def engineer(df, cols):
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[f"{c}_mean5m"]  = df[c].rolling(5,  min_periods=1).mean()
            df[f"{c}_mean15m"] = df[c].rolling(15, min_periods=1).mean()
            df[f"{c}_std15m"]  = df[c].rolling(15, min_periods=1).std().fillna(0)
            df[f"{c}_delta"]   = df[c].diff().fillna(0)
    return df


def classify_type(row):
    cpu  = row.get("pct_processor_time", 0)
    mem  = row.get("available_mbytes", 9999)
    disk = row.get("pct_free_space", 100)
    dq   = row.get("current_disk_queue_length", 0)
    net  = row.get("bytes_received_per_sec", 0) + row.get("bytes_sent_per_sec", 0)
    if cpu > 85 or mem < 512 or disk < 15 or dq > 10:
        return "resource_exhaustion"
    if net == 0 or net > 1_000_000_000:
        return "network_misconfiguration"
    return "config_drift"


def severity(score, row):
    cpu  = row.get("pct_processor_time", 0)
    mem  = row.get("available_mbytes", 9999)
    disk = row.get("pct_free_space", 100)
    if cpu > 95 or mem < 256 or disk < 5:
        return "critical"
    if cpu > 85 or mem < 512 or disk < 15:
        return "high"
    if score < -0.2:
        return "medium"
    return "low"


def predict(env, workspace_id=None, minutes=10, use_file=False):
    if_model, lstm_model, scaler, meta = load_models(env)
    feat_cols      = meta["feature_cols"]
    lstm_threshold = meta.get("lstm_threshold")

    df = load_from_file(env) if (use_file or not workspace_id) \
         else fetch_live(workspace_id, minutes)

    if df is None or df.empty:
        return {"status":"no_data","env":env,
                "timestamp":datetime.now(timezone.utc).isoformat()}

    base_cols = [c for c in FEATURE_COLS if c in df.columns]
    df = engineer(df, base_cols)
    for c in feat_cols:
        if c not in df.columns:
            df[c] = 0.0
    df = df.fillna(0)
    X  = df[feat_cols].values
    if len(X) == 0:
        return {"status":"no_data","env":env,
                "timestamp":datetime.now(timezone.utc).isoformat()}

    X_s      = scaler.transform(X)
    if_preds = if_model.predict(X_s)
    if_scores= if_model.score_samples(X_s)
    if_labels= np.where(if_preds == -1, 1, 0)

    latest_label = int(if_labels[-1])
    latest_score = float(if_scores[-1])
    latest_row   = df.iloc[-1]

    lstm_label = None
    lstm_error = None
    if lstm_model is not None and len(X_s) >= SEQUENCE_LENGTH:
        seq   = X_s[-SEQUENCE_LENGTH:].reshape(1, SEQUENCE_LENGTH, -1)
        recon = lstm_model.predict(seq, verbose=0)
        lstm_error = float(np.mean(np.power(seq - recon, 2)))
        lstm_label = 1 if (lstm_threshold and lstm_error > lstm_threshold) else 0

    is_anomaly = bool(latest_label == 1 or (lstm_label is not None and lstm_label == 1))
    atype      = classify_type(latest_row) if is_anomaly else "none"
    sev        = severity(latest_score, latest_row) if is_anomaly else "none"

    return {
        "status"      : "anomaly" if is_anomaly else "normal",
        "env"         : env,
        "timestamp"   : datetime.now(timezone.utc).isoformat(),
        "is_anomaly"  : is_anomaly,
        "anomaly_type": atype,
        "severity"    : sev,
        "models": {
            "isolation_forest": {
                "label": latest_label, "score": round(latest_score, 6),
                "is_anomaly": bool(latest_label == 1)
            },
            "lstm_autoencoder": {
                "label": lstm_label,
                "reconstruction_error": round(lstm_error,6) if lstm_error else None,
                "threshold": lstm_threshold,
                "is_anomaly": bool(lstm_label==1) if lstm_label is not None else None
            }
        },
        "current_metrics": {
            "cpu_pct":             round(float(latest_row.get("pct_processor_time",0)),2),
            "memory_available_mb": round(float(latest_row.get("available_mbytes",0)),2),
            "disk_free_pct":       round(float(latest_row.get("pct_free_space",0)),2),
            "disk_queue":          round(float(latest_row.get("current_disk_queue_length",0)),2),
            "net_rx":              round(float(latest_row.get("bytes_received_per_sec",0)),2),
            "net_tx":              round(float(latest_row.get("bytes_sent_per_sec",0)),2),
        },
        "n_anomalies_last_hour": int(if_labels[-60:].sum()) if len(if_labels)>=60 else int(if_labels.sum()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="dev", choices=["dev","test","prod"])
    parser.add_argument("--workspace-id")
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--output", default="pretty", choices=["pretty","json"])
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()

    def run_once():
        r = predict(args.env, args.workspace_id, args.minutes, args.latest)
        if args.output == "json":
            print(json.dumps(r, indent=2))
        else:
            icon = "ANOMALY" if r.get("is_anomaly") else "NORMAL"
            print(f"\n[{r['timestamp']}] {args.env.upper()} — {icon}")
            if r.get("is_anomaly"):
                print(f"  Type    : {r['anomaly_type']}")
                print(f"  Severity: {r['severity']}")
            m = r.get("current_metrics", {})
            print(f"  CPU     : {m.get('cpu_pct')}%")
            print(f"  Memory  : {m.get('memory_available_mb')} MB free")
            print(f"  Disk    : {m.get('disk_free_pct')}% free")
            print(f"  IF Score: {r['models']['isolation_forest']['score']}")
        return r

    if args.watch:
        print("Watch mode — Ctrl+C to stop")
        while True:
            run_once()
            time.sleep(60)
    else:
        r = run_once()
        sys.exit(1 if r.get("is_anomaly") else 0)


if __name__ == "__main__":
    main()
