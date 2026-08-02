"""
ml/pretrain.py
Pre-trains ML models on Azure VM Noise Dataset (CPU stress data).
Uses the publicly available AzureVMNoiseDataset2024 from Microsoft.
"""

import json
import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

ML_DIR    = Path(__file__).parent
DATA_DIR  = ML_DIR / "data" / "azure_public"
MODEL_DIR = ML_DIR / "models"
MODEL_DIR.mkdir(exist_ok=True)

def load_azure_dataset():
    """Load Azure VM Noise Dataset CSV files."""
    cpu_file = DATA_DIR / "cpu_stress_eastus.csv"
    print(f"Loading: {cpu_file}")
    df = pd.read_csv(cpu_file)
    print(f"  Rows: {len(df)}")
    print(f"  Columns: {list(df.columns)}")
    print(f"  Value range: {df['value'].min():.1f} - {df['value'].max():.1f}")
    return df

def engineer_features(df):
    """Engineer features from CPU stress time series."""
    df = df.copy()
    df['starttime'] = pd.to_datetime(df['starttime'])
    df = df.sort_values('starttime').reset_index(drop=True)

    # Core feature — CPU performance (Bogo Ops/s)
    df['cpu_perf']       = df['value']

    # Rolling statistics — capture degradation patterns
    df['cpu_mean_10']    = df['value'].rolling(10, min_periods=1).mean()
    df['cpu_std_10']     = df['value'].rolling(10, min_periods=1).std().fillna(0)
    df['cpu_min_10']     = df['value'].rolling(10, min_periods=1).min()
    df['cpu_delta']      = df['value'].diff().fillna(0)
    df['cpu_pct_change'] = df['value'].pct_change().fillna(0).clip(-1, 1)

    # Anomaly label — values more than 3 std below mean are anomalies
    global_mean = df['value'].mean()
    global_std  = df['value'].std()
    df['anomaly_label'] = (df['value'] < global_mean - 3 * global_std).astype(int)

    n_anomalies = df['anomaly_label'].sum()
    print(f"  Anomalies detected: {n_anomalies}/{len(df)} "
          f"({n_anomalies/len(df)*100:.1f}%)")

    return df

FEATURE_COLS = [
    'cpu_perf', 'cpu_mean_10', 'cpu_std_10',
    'cpu_min_10', 'cpu_delta', 'cpu_pct_change'
]

def main():
    print("\n" + "="*55)
    print("  Azure VM Noise Dataset — Pre-training")
    print("="*55 + "\n")

    # Load data
    print("Step 1: Loading Azure VM Noise Dataset...")
    df = load_azure_dataset()

    # Engineer features
    print("\nStep 2: Engineering features...")
    df = engineer_features(df)

    # Split — use VM_id=0 for training, VM_id=1 for validation
    train_df = df[df['VM_id'] == 0].copy()
    val_df   = df[df['VM_id'] == 1].copy()
    print(f"  Train samples: {len(train_df)}")
    print(f"  Val samples  : {len(val_df)}")

    X_train = train_df[FEATURE_COLS].fillna(0).values
    X_val   = val_df[FEATURE_COLS].fillna(0).values
    y_val   = val_df['anomaly_label'].values

    # Scale
    print("\nStep 3: Scaling features...")
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s   = scaler.transform(X_val)

    # Train Isolation Forest
    print("\nStep 4: Training Isolation Forest...")
    model = IsolationForest(
        n_estimators=200,
        contamination=0.05,
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_train_s)

    # Evaluate on validation set
    preds  = model.predict(X_val_s)
    labels = np.where(preds == -1, 1, 0)
    scores = model.score_samples(X_val_s)

    if y_val.sum() > 0:
        from sklearn.metrics import precision_recall_fscore_support, roc_auc_score
        p, r, f1, _ = precision_recall_fscore_support(
            y_val, labels, average='binary', zero_division=0)
        try:
            auc = roc_auc_score(y_val, -scores)
        except Exception:
            auc = None
        print(f"\n  Pre-training Results:")
        print(f"    Precision : {p:.4f}")
        print(f"    Recall    : {r:.4f}")
        print(f"    F1        : {f1:.4f}")
        print(f"    AUC       : {auc}")

    # Save pretrained models
    print("\nStep 5: Saving pretrained models...")
    with open(MODEL_DIR / "pretrained_isolation_forest.pkl", "wb") as f:
        pickle.dump(model, f)
    with open(MODEL_DIR / "pretrained_scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)

    meta = {
        "pretrained_on"  : "AzureVMNoiseDataset2024",
        "dataset_url"    : "https://github.com/Azure/AzurePublicDataset",
        "license"        : "CC-BY-4.0",
        "n_train_samples": int(len(X_train)),
        "feature_cols"   : FEATURE_COLS,
        "trained_at"     : pd.Timestamp.now().isoformat()
    }
    with open(MODEL_DIR / "pretrained_metadata.json", "w") as f:
        json.dump(meta, f, indent=2)

    print(f"  Saved to: {MODEL_DIR}")
    print(f"\n  Pre-training complete.")
    print(f"  Next: Deploy dev VM → collect telemetry → fine-tune")

if __name__ == "__main__":
    main()
