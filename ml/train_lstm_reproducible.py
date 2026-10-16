#!/usr/bin/env python3
"""Reproducible LSTM autoencoder evaluation for the GHES self-healing paper.

Data  : AzureVMNoiseDataset2024, Stress-NG CPU test, East US, D8s_v5 (ml/data/azure_public/cpu_stress_eastus.csv)
Labels: 3-sigma rule on CPU Bogo Ops/s (same labels as the paper; they are derived from an input feature,
        so results are a pipeline/feasibility check, not evidence of detecting real GHES failures).
Split : VM_id=0 -> fit (first 70%), early-stopping validation (next 15%), threshold calibration (last 15%).
        VM_id=1 -> untouched test set. The threshold is the CAL_PCT percentile of reconstruction errors on the
        calibration windows (no labels used, no tuning on the test set).
Run   : cd ml && python train_lstm_reproducible.py --csv data/azure_public/cpu_stress_eastus.csv --out results --seeds 5
"""
import argparse, json, os, random, time
import numpy as np, pandas as pd
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
import tensorflow as tf
from tensorflow.keras.models import Model
from tensorflow.keras.layers import Input, LSTM, Dense, RepeatVector, TimeDistributed
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (precision_recall_fscore_support, roc_auc_score, average_precision_score,
                             roc_curve, confusion_matrix)

SEQ_LEN, HIDDEN, LR, CLIP, BATCH, MAX_EPOCHS, PATIENCE, CAL_PCT = 30, 32, 5e-4, 1.0, 64, 80, 8, 95
FEATS = ['cpu_perf', 'cpu_mean_10', 'cpu_std_10', 'cpu_min_10', 'cpu_delta', 'cpu_pct_change']

def load(csv):
    df = pd.read_csv(csv)
    df['starttime'] = pd.to_datetime(df['starttime'])
    df = df.sort_values('starttime').reset_index(drop=True)
    v = df['value']
    df['cpu_perf'] = v
    df['cpu_mean_10'] = v.rolling(10, min_periods=1).mean()
    df['cpu_std_10'] = v.rolling(10, min_periods=1).std().fillna(0)
    df['cpu_min_10'] = v.rolling(10, min_periods=1).min()
    df['cpu_delta'] = v.diff().fillna(0)
    df['cpu_pct_change'] = v.pct_change().fillna(0).clip(-1, 1)
    mu, sd = v.mean(), v.std()
    df['y'] = (v < mu - 3 * sd).astype(int)
    return df, mu, sd

def windows(X, y, L):
    xs = np.array([X[i:i + L] for i in range(len(X) - L + 1)])
    ys = np.array([y[i + L - 1] for i in range(len(X) - L + 1)])   # label of the window's last observation
    return xs, ys

def build():
    inp = Input(shape=(SEQ_LEN, len(FEATS)))
    enc = LSTM(HIDDEN, activation='tanh')(inp)
    rep = RepeatVector(SEQ_LEN)(enc)
    dec = LSTM(HIDDEN, activation='tanh', return_sequences=True)(rep)
    out = TimeDistributed(Dense(len(FEATS)))(dec)
    m = Model(inp, out)
    m.compile(optimizer=Adam(learning_rate=LR, clipnorm=CLIP), loss='mse')
    return m

def errs(m, X):
    return np.mean((X - m.predict(X, verbose=0)) ** 2, axis=(1, 2))

def run(df, seed, outdir, save_curves):
    random.seed(seed); np.random.seed(seed); tf.keras.utils.set_random_seed(seed)
    tr, te = df[df.VM_id == 0], df[df.VM_id == 1]
    sc = StandardScaler().fit(tr[FEATS].values)
    Xtr, ytr = windows(sc.transform(tr[FEATS].values), tr['y'].values, SEQ_LEN)
    Xte, yte = windows(sc.transform(te[FEATS].values), te['y'].values, SEQ_LEN)
    keep = ytr == 0                                   # fit on windows whose last point is normal
    n = len(Xtr); a, b = int(.70 * n), int(.85 * n)   # chronological: fit | early-stop val | calibration
    Xfit, Xval, Xcal = Xtr[:a][keep[:a]], Xtr[a:b][keep[a:b]], Xtr[b:][keep[b:]]
    m = build(); t0 = time.time()
    h = m.fit(Xfit, Xfit, validation_data=(Xval, Xval), epochs=MAX_EPOCHS, batch_size=BATCH, shuffle=True,
              callbacks=[EarlyStopping(patience=PATIENCE, restore_best_weights=True)], verbose=0)
    tt = round(time.time() - t0, 1)
    thr = float(np.percentile(errs(m, Xcal), CAL_PCT))
    e = errs(m, Xte); pred = (e > thr).astype(int)
    p, r, f1, _ = precision_recall_fscore_support(yte, pred, average='binary', zero_division=0)
    tn, fp, fn, tp = confusion_matrix(yte, pred, labels=[0, 1]).ravel()
    res = dict(seed=seed, epochs_run=len(h.history['loss']), best_epoch=int(np.argmin(h.history['val_loss']) + 1),
               final_val_loss=float(min(h.history['val_loss'])), train_seconds=tt, threshold=thr,
               n_test_windows=int(len(yte)), n_test_anomalies=int(yte.sum()),
               precision=float(p), recall=float(r), f1=float(f1), roc_auc=float(roc_auc_score(yte, e)),
               pr_auc=float(average_precision_score(yte, e)), tp=int(tp), fp=int(fp), fn=int(fn), tn=int(tn),
               false_alert_rate=float(fp / max(1, (yte == 0).sum())))
    if save_curves:
        fpr, tpr, _ = roc_curve(yte, e)
        np.savez(os.path.join(outdir, 'curves_seed%d.npz' % seed), loss=h.history['loss'], val_loss=h.history['val_loss'],
                 fpr=fpr, tpr=tpr, err_normal=e[yte == 0], err_anom=e[yte == 1], thr=thr)
    return res

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--csv', required=True); ap.add_argument('--out', default='results')
    ap.add_argument('--seeds', type=int, default=5); a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    df, mu, sd = load(a.csv)
    print('n=%d  3-sigma threshold=%.2f  anomalies=%d  per-VM anomalies=%s' % (len(df), mu - 3 * sd, df.y.sum(),
          df.groupby('VM_id').y.sum().to_dict()))
    runs = [run(df, s, a.out, s == 0) for s in range(a.seeds)]
    keys = ['precision', 'recall', 'f1', 'roc_auc', 'pr_auc', 'fp', 'fn', 'tp', 'epochs_run', 'best_epoch', 'train_seconds']
    summ = {k: [float(np.mean([r[k] for r in runs])), float(np.std([r[k] for r in runs]))] for k in keys}
    cfg = dict(seq_len=SEQ_LEN, hidden=HIDDEN, activation='tanh', lr=LR, clipnorm=CLIP, batch=BATCH,
               max_epochs=MAX_EPOCHS, patience=PATIENCE, threshold='%dth percentile of calibration-window errors' % CAL_PCT,
               tensorflow=tf.__version__)
    json.dump(dict(config=cfg, runs=runs, mean_std=summ), open(os.path.join(a.out, 'lstm_results.json'), 'w'), indent=2)
    for r in runs: print(r)
    print('MEAN+-SD', {k: (round(v[0], 4), round(v[1], 4)) for k, v in summ.items()})
