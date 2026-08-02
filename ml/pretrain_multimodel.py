"""
ml/pretrain_multimodel.py
Trains and compares multiple anomaly detection models on AzureVMNoiseDataset2024.
"""

import json
import pickle
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import precision_recall_fscore_support, roc_auc_score
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

warnings.filterwarnings("ignore")

ML_DIR    = Path(__file__).parent
DATA_DIR  = ML_DIR / "data" / "azure_public"
MODEL_DIR = ML_DIR / "models"
MODEL_DIR.mkdir(exist_ok=True)

FEATURE_COLS = [
    'cpu_perf', 'cpu_mean_10', 'cpu_std_10',
    'cpu_min_10', 'cpu_delta', 'cpu_pct_change'
]

MODEL_RATIONALE = {
    "OneClassSVM": {
        "justification": "Kernel-based unsupervised baseline. Learns a decision boundary around normal data without anomaly labels. RBF kernel captures non-linear relationships between CPU performance metrics.",
        "limitations": "Scales poorly to large datasets O(n^2). Requires careful tuning of nu and gamma. Assumes a single compact cluster of normal data which may not hold for heterogeneous VM workloads.",
        "why_chosen": "Kernel-based boundary learning — no distributional assumption required"
    },
    "RandomForest": {
        "justification": "Supervised baseline using 3-sigma anomaly labels. Interpretable — feature importances reveal which metrics most strongly predict anomalies. Handles class imbalance via class_weight=balanced.",
        "limitations": "Requires labeled training data. Treats each observation independently ignoring temporal dependencies — significant limitation for time-series infrastructure metrics where gradual degradation unfolds over minutes to hours.",
        "why_chosen": "Supervised baseline — interpretable feature importances for paper analysis"
    },
    "IsolationForest": {
        "justification": "Primary unsupervised model. Linear time complexity O(n log n), low memory footprint, strong performance on high-dimensional tabular data. Robust to extreme class imbalance (0.2% anomaly rate) in production infrastructure.",
        "limitations": "Assumes anomalies are globally rare and isolated. May not capture clustered anomaly patterns such as cascading failures. Operates on fixed feature windows without modeling temporal dependencies.",
        "why_chosen": "Primary model — O(n log n) complexity, robust to extreme class imbalance"
    }
}

def load_and_engineer():
    cpu_file = DATA_DIR / "cpu_stress_eastus.csv"
    df = pd.read_csv(cpu_file)
    df['starttime'] = pd.to_datetime(df['starttime'])
    df = df.sort_values('starttime').reset_index(drop=True)

    df['cpu_perf']       = df['value']
    df['cpu_mean_10']    = df['value'].rolling(10, min_periods=1).mean()
    df['cpu_std_10']     = df['value'].rolling(10, min_periods=1).std().fillna(0)
    df['cpu_min_10']     = df['value'].rolling(10, min_periods=1).min()
    df['cpu_delta']      = df['value'].diff().fillna(0)
    df['cpu_pct_change'] = df['value'].pct_change().fillna(0).clip(-1, 1)

    global_mean = df['value'].mean()
    global_std  = df['value'].std()
    df['anomaly_label'] = (df['value'] < global_mean - 3 * global_std).astype(int)

    print(f"  Loaded {len(df)} observations")
    print(f"  Anomalies: {df['anomaly_label'].sum()} ({df['anomaly_label'].mean()*100:.1f}%)")

    train_df = df[df['VM_id'] == 0].copy()
    val_df   = df[df['VM_id'] == 1].copy()

    X_train = train_df[FEATURE_COLS].fillna(0).values
    X_val   = val_df[FEATURE_COLS].fillna(0).values
    y_train = train_df['anomaly_label'].values
    y_val   = val_df['anomaly_label'].values

    scaler    = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s   = scaler.transform(X_val)

    return X_train_s, X_val_s, y_train, y_val, scaler

def evaluate(name, y_true, y_pred, scores=None):
    p, r, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average='binary', zero_division=0)
    auc = None
    if scores is not None and y_true.sum() > 0:
        try:
            auc = roc_auc_score(y_true, scores)
        except Exception:
            pass
    return {
        "model"    : name,
        "precision": round(float(p), 4),
        "recall"   : round(float(r), 4),
        "f1"       : round(float(f1), 4),
        "auc"      : round(float(auc), 4) if auc else None,
    }

def train_ocsvm(X_train, X_val, y_val):
    print("\nMODEL 1: One-Class SVM")
    print(f"  Justification: {MODEL_RATIONALE['OneClassSVM']['why_chosen']}")
    t0 = time.time()
    model = OneClassSVM(kernel='rbf', nu=0.05, gamma='scale')
    model.fit(X_train)
    t = round(time.time()-t0, 2)
    preds  = model.predict(X_val)
    labels = np.where(preds == -1, 1, 0)
    scores = -model.decision_function(X_val)
    r = evaluate("One-Class SVM", y_val, labels, scores)
    r["train_time_seconds"] = t
    r["justification"] = MODEL_RATIONALE['OneClassSVM']['justification']
    r["limitations"]   = MODEL_RATIONALE['OneClassSVM']['limitations']
    print(f"  Time: {t}s | P:{r['precision']} R:{r['recall']} F1:{r['f1']} AUC:{r['auc']}")
    print(f"  LIMITATION: {r['limitations'][:100]}...")
    with open(MODEL_DIR / "pretrained_ocsvm.pkl", "wb") as f:
        pickle.dump(model, f)
    return r

def train_random_forest(X_train, X_val, y_train, y_val):
    print("\nMODEL 2: Random Forest")
    print(f"  Justification: {MODEL_RATIONALE['RandomForest']['why_chosen']}")
    t0 = time.time()
    model = RandomForestClassifier(n_estimators=200, class_weight='balanced',
                                   random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)
    t = round(time.time()-t0, 2)
    preds  = model.predict(X_val)
    scores = model.predict_proba(X_val)[:, 1]
    r = evaluate("Random Forest", y_val, preds, scores)
    r["train_time_seconds"] = t
    r["feature_importances"] = dict(zip(FEATURE_COLS,
                                        model.feature_importances_.round(4).tolist()))
    r["justification"] = MODEL_RATIONALE['RandomForest']['justification']
    r["limitations"]   = MODEL_RATIONALE['RandomForest']['limitations']
    print(f"  Time: {t}s | P:{r['precision']} R:{r['recall']} F1:{r['f1']} AUC:{r['auc']}")
    print(f"  Feature importances: {r['feature_importances']}")
    print(f"  LIMITATION: {r['limitations'][:100]}...")
    with open(MODEL_DIR / "pretrained_rf.pkl", "wb") as f:
        pickle.dump(model, f)
    return r

def train_isolation_forest(X_train, X_val, y_val):
    print("\nMODEL 3: Isolation Forest (primary)")
    print(f"  Justification: {MODEL_RATIONALE['IsolationForest']['why_chosen']}")
    t0 = time.time()
    model = IsolationForest(n_estimators=200, contamination=0.05,
                            random_state=42, n_jobs=-1)
    model.fit(X_train)
    t = round(time.time()-t0, 2)
    preds  = model.predict(X_val)
    labels = np.where(preds == -1, 1, 0)
    scores = -model.score_samples(X_val)
    r = evaluate("Isolation Forest", y_val, labels, scores)
    r["train_time_seconds"] = t
    r["justification"] = MODEL_RATIONALE['IsolationForest']['justification']
    r["limitations"]   = MODEL_RATIONALE['IsolationForest']['limitations']
    print(f"  Time: {t}s | P:{r['precision']} R:{r['recall']} F1:{r['f1']} AUC:{r['auc']}")
    print(f"  LIMITATION: {r['limitations'][:100]}...")
    with open(MODEL_DIR / "pretrained_isolation_forest.pkl", "wb") as f:
        pickle.dump(model, f)
    return r

def print_latex_table(results):
    print("\n" + "="*60)
    print("  LATEX TABLE — copy into paper Section IV")
    print("="*60)
    print(r"""
\begin{table}[h]
\caption{Multi-Model Anomaly Detection on AzureVMNoiseDataset2024}
\label{tab:multimodel}
\centering
\begin{tabular}{lcccc}
\toprule
\textbf{Model} & \textbf{Prec.} & \textbf{Rec.} & \textbf{F1} & \textbf{AUC} \\
\midrule""")
    for r in results:
        auc = str(r['auc']) if r['auc'] else "N/A"
        print(f"{r['model']} & {r['precision']} & {r['recall']} & {r['f1']} & {auc} \\\\")
    print(r"""\bottomrule
\end{tabular}
\end{table}""")

def main():
    print("\n" + "="*60)
    print("  MULTI-MODEL PRE-TRAINING")
    print("  Dataset: AzureVMNoiseDataset2024 (CC-BY-4.0)")
    print("="*60 + "\n")

    print("Loading data...")
    X_train, X_val, y_train, y_val, scaler = load_and_engineer()

    with open(MODEL_DIR / "pretrained_scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)

    results = [
        train_ocsvm(X_train, X_val, y_val),
        train_random_forest(X_train, X_val, y_train, y_val),
        train_isolation_forest(X_train, X_val, y_val),
    ]

    print_latex_table(results)

    out = {
        "dataset": "AzureVMNoiseDataset2024",
        "n_observations": 10406,
        "trained_at": pd.Timestamp.now().isoformat(),
        "results": results,
        "model_rationale": MODEL_RATIONALE
    }
    with open(MODEL_DIR / "multimodel_comparison.json", "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nDone. Results: {MODEL_DIR}/multimodel_comparison.json")
    print("Note: LSTM and Dense Autoencoder require Python 3.11 + TensorFlow")
    print("Run on Colab or install Python 3.11 to train deep learning models")

if __name__ == "__main__":
    main()
