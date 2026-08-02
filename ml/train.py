"""
ml/train.py
Trains Isolation Forest + LSTM Autoencoder on GHES VM telemetry.
"""

import argparse
import json
import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (confusion_matrix,
                             precision_recall_fscore_support,
                             roc_auc_score)
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

ML_DIR    = Path(__file__).parent
DATA_DIR  = ML_DIR / "data"
MODEL_DIR = ML_DIR / "models"
MODEL_DIR.mkdir(exist_ok=True)

FEATURE_COLS = [
    "pct_processor_time",
    "available_mbytes",
    "pct_free_space",
    "disk_read_bytes_per_sec",
    "disk_write_bytes_per_sec",
    "current_disk_queue_length",
    "bytes_received_per_sec",
    "bytes_sent_per_sec",
]
SEQUENCE_LENGTH = 60


def load_features(env):
    path = DATA_DIR / f"{env}_features.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No feature file: {path}\n"
            f"Run: python ml/collect_data.py --workspace-id <id> --env {env}"
        )
    df = pd.read_csv(path, parse_dates=["TimeGenerated"])
    df = df.sort_values("TimeGenerated").reset_index(drop=True)
    print(f"  Loaded {len(df)} rows")
    available = [c for c in FEATURE_COLS if c in df.columns]
    for col in FEATURE_COLS:
        if col not in df.columns:
            df[col] = 0.0
    return df, available


def engineer_features(df, feature_cols):
    df = df.copy()
    for col in feature_cols:
        if col not in df.columns:
            continue
        df[f"{col}_mean5m"]  = df[col].rolling(5,  min_periods=1).mean()
        df[f"{col}_mean15m"] = df[col].rolling(15, min_periods=1).mean()
        df[f"{col}_std15m"]  = df[col].rolling(15, min_periods=1).std().fillna(0)
        df[f"{col}_delta"]   = df[col].diff().fillna(0)
    return df


def get_all_cols(feature_cols):
    derived = []
    for col in feature_cols:
        derived += [f"{col}_mean5m", f"{col}_mean15m",
                    f"{col}_std15m", f"{col}_delta"]
    return feature_cols + derived


def train_isolation_forest(X_train, contamination=0.05):
    print(f"  Training Isolation Forest (contamination={contamination})...")
    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        max_samples="auto",
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_train)
    print(f"  Trained on {X_train.shape[0]} samples, {X_train.shape[1]} features")
    return model


def evaluate_isolation_forest(model, X_test, y_test):
    raw_preds = model.predict(X_test)
    y_pred    = np.where(raw_preds == -1, 1, 0)
    scores    = model.score_samples(X_test)
    p, r, f1, _ = precision_recall_fscore_support(
        y_test, y_pred, average="binary", zero_division=0)
    results = {
        "model": "IsolationForest",
        "precision": round(float(p), 4),
        "recall":    round(float(r), 4),
        "f1":        round(float(f1), 4),
    }
    try:
        results["roc_auc"] = round(roc_auc_score(y_test, -scores), 4)
    except Exception:
        results["roc_auc"] = None
    print(f"\n  IF Results — P:{results['precision']} R:{results['recall']} F1:{results['f1']} AUC:{results['roc_auc']}")
    print(f"  Confusion matrix:\n{confusion_matrix(y_test, y_pred)}")
    return results


def build_sequences(data, seq_len):
    return np.array([data[i:i+seq_len] for i in range(len(data)-seq_len)])


def train_lstm(X_train_seq, epochs=30, batch_size=32):
    try:
        import tensorflow as tf
        from tensorflow.keras.callbacks import EarlyStopping
        from tensorflow.keras.layers import (LSTM, Dense, Input,
                                             RepeatVector, TimeDistributed)
        from tensorflow.keras.models import Model
        from tensorflow.keras.optimizers import Adam
    except ImportError:
        print("  TensorFlow not installed: pip install tensorflow")
        return None, None

    n_features = X_train_seq.shape[2]
    seq_len    = X_train_seq.shape[1]
    print(f"  LSTM: seq={seq_len} features={n_features} samples={len(X_train_seq)}")

    inp      = Input(shape=(seq_len, n_features))
    enc      = LSTM(64, activation="relu", return_sequences=False)(inp)
    rep      = RepeatVector(seq_len)(enc)
    dec      = LSTM(64, activation="relu", return_sequences=True)(rep)
    out      = TimeDistributed(Dense(n_features))(dec)
    model    = Model(inp, out)
    model.compile(optimizer=Adam(0.001), loss="mse")

    model.fit(
        X_train_seq, X_train_seq,
        epochs=epochs,
        batch_size=batch_size,
        validation_split=0.1,
        callbacks=[EarlyStopping(patience=5, restore_best_weights=True)],
        verbose=1
    )
    return model, None


def get_errors(model, X_seq):
    recon = model.predict(X_seq, verbose=0)
    return np.mean(np.power(X_seq - recon, 2), axis=(1, 2))


def evaluate_lstm(model, X_test_seq, y_test_seq, threshold=None):
    errors = get_errors(model, X_test_seq)
    if threshold is None:
        threshold = float(np.mean(errors) + 2 * np.std(errors))
    y_pred = (errors > threshold).astype(int)
    p, r, f1, _ = precision_recall_fscore_support(
        y_test_seq, y_pred, average="binary", zero_division=0)
    results = {
        "model": "LSTM_Autoencoder",
        "threshold": round(threshold, 6),
        "precision": round(float(p), 4),
        "recall":    round(float(r), 4),
        "f1":        round(float(f1), 4),
    }
    try:
        results["roc_auc"] = round(roc_auc_score(y_test_seq, errors), 4)
    except Exception:
        results["roc_auc"] = None
    print(f"\n  LSTM Results — P:{results['precision']} R:{results['recall']} F1:{results['f1']} AUC:{results['roc_auc']}")
    print(f"  Confusion matrix:\n{confusion_matrix(y_test_seq, y_pred)}")
    return results, threshold


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="dev",
                        choices=["dev", "test", "prod"])
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--contamination", type=float, default=0.05)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--test-split", type=float, default=0.2)
    args = parser.parse_args()

    print(f"\n{'='*50}")
    print(f"  GHES ML Training — {args.env}")
    print(f"{'='*50}\n")

    print("Step 1: Loading data...")
    df, available_cols = load_features(args.env)

    print("\nStep 2: Engineering features...")
    df = engineer_features(df, available_cols)
    all_cols = [c for c in get_all_cols(available_cols) if c in df.columns]
    print(f"  Total features: {len(all_cols)}")

    X = df[all_cols].values
    split = int(len(X) * (1 - args.test_split))
    X_train, X_test = X[:split], X[split:]

    has_labels = "anomaly_label" in df.columns
    y_train = df["anomaly_label"].values[:split] if has_labels else None
    y_test  = df["anomaly_label"].values[split:] if has_labels else None

    print(f"  Train: {len(X_train)}  Test: {len(X_test)}")

    print("\nStep 3: Scaling...")
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    print("\nStep 4: Isolation Forest...")
    if_model  = train_isolation_forest(X_train_s, args.contamination)
    if_results = {}
    if args.evaluate and has_labels:
        if_results = evaluate_isolation_forest(if_model, X_test_s, y_test)

    print("\nStep 5: LSTM Autoencoder...")
    lstm_model = None
    lstm_threshold = None
    lstm_results   = {}

    X_lstm = X_train_s[y_train == 0] if has_labels else X_train_s
    if len(X_lstm) >= SEQUENCE_LENGTH * 2:
        X_seq = build_sequences(X_lstm, SEQUENCE_LENGTH)
        lstm_model, _ = train_lstm(X_seq, epochs=args.epochs)
        if lstm_model:
            train_errors   = get_errors(lstm_model, X_seq)
            lstm_threshold = float(np.mean(train_errors) + 2*np.std(train_errors))
            if args.evaluate and has_labels:
                X_ts  = build_sequences(X_test_s, SEQUENCE_LENGTH)
                y_ts  = y_test[SEQUENCE_LENGTH:]
                lstm_results, lstm_threshold = evaluate_lstm(
                    lstm_model, X_ts, y_ts, lstm_threshold)
    else:
        print(f"  Not enough data ({len(X_lstm)} rows, need {SEQUENCE_LENGTH*2})")

    print("\nStep 6: Saving...")
    with open(MODEL_DIR / f"{args.env}_isolation_forest.pkl", "wb") as f:
        pickle.dump(if_model, f)
    with open(MODEL_DIR / f"{args.env}_scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)
    if lstm_model:
        lstm_model.save(str(MODEL_DIR / f"{args.env}_lstm_autoencoder.keras"))

    meta = {
        "env": args.env,
        "trained_at": pd.Timestamp.now().isoformat(),
        "feature_cols": all_cols,
        "n_train_samples": int(len(X_train)),
        "lstm_threshold": lstm_threshold,
        "contamination": args.contamination,
        "if_results": if_results,
        "lstm_results": lstm_results,
    }
    with open(MODEL_DIR / f"{args.env}_metadata.json", "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\nModels saved to {MODEL_DIR}")
    if if_results:
        print(f"IF F1: {if_results.get('f1')}")
    if lstm_results:
        print(f"LSTM F1: {lstm_results.get('f1')}")
    print(f"\nNext: python ml/predict.py --env {args.env} --latest")


if __name__ == "__main__":
    main()
