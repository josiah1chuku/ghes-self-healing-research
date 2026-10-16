"""Reproduces Table II (dataset statistics) and Fig. 3 (feature analysis) of the paper.

Run from the repository root:  python ml/make_feature_figure.py
Reads  ml/data/azure_public/cpu_stress_eastus.csv  and  ml/models/multimodel_comparison.json
Writes fig/fig4_feature_analysis.pdf and prints the Table II values.
Labels follow the paper: 3-sigma rule on the CPU value over all observations.
"""
import json, os
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, 'ml', 'data', 'azure_public', 'cpu_stress_eastus.csv')
MODELS = os.path.join(ROOT, 'ml', 'models', 'multimodel_comparison.json')
OUT = os.path.join(ROOT, 'fig', 'fig4_feature_analysis.pdf')
F = ['cpu_perf', 'cpu_mean_10', 'cpu_std_10', 'cpu_min_10', 'cpu_delta', 'cpu_pct_change']
NAMES = ['perf', 'mean10', 'std10', 'min10', 'delta', 'pct']

df = pd.read_csv(CSV)
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

print(f'All: n={len(df)} mean={mu:.1f} std={sd:.1f} threshold={mu-3*sd:.1f} anomalies={df.y.sum()}')
for vm, g in df.groupby('VM_id'):
    print(f'VM{vm}: n={len(g)} mean={g.value.mean():.1f} std={g.value.std():.1f} '
          f'anomalies={g.y.sum()} rate={100*g.y.mean():.2f}%')

C = df[F + ['y']].corr(method='pearson').values
imp = json.load(open(MODELS))['results'][1]['feature_importances']  # Random Forest, VM 0, saved run

matplotlib.rcParams.update({'pdf.fonttype': 42, 'font.family': ['Liberation Sans', 'DejaVu Sans'],
                            'font.size': 6, 'axes.linewidth': 0.6})
fig, (a1, a2) = plt.subplots(1, 2, figsize=(3.45, 1.75), gridspec_kw={'width_ratios': [1.15, 1]})
lab = NAMES + ['label']
a1.imshow(C, cmap='RdBu_r', vmin=-1, vmax=1)
a1.set_xticks(range(7)); a1.set_xticklabels(lab, rotation=60, ha='right', fontsize=4.8)
a1.set_yticks(range(7)); a1.set_yticklabels(lab, fontsize=4.8)
for i in range(7):
    for j in range(7):
        a1.text(j, i, f'{C[i, j]:.2f}', ha='center', va='center', fontsize=3.9,
                color='white' if abs(C[i, j]) > 0.6 else 'black')
a1.set_title('Pearson correlation (n = 10,406)', fontsize=5.8)
order = sorted(F, key=lambda k: imp[k])
a2.barh([NAMES[F.index(k)] for k in order], [imp[k] for k in order], color='#1f77b4', height=0.65)
for i, k in enumerate(order):
    a2.text(imp[k] + 0.008, i, f'{imp[k]*100:.1f}%', va='center', fontsize=4.8)
a2.set_xlim(0, 0.45); a2.set_xlabel('Random Forest importance', fontsize=5.5)
a2.tick_params(labelsize=5); a2.set_title('Feature importance', fontsize=5.8)
for s in ('top', 'right'):
    a2.spines[s].set_visible(False)
fig.tight_layout(pad=0.4, w_pad=0.8)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
fig.savefig(OUT)
print('wrote', OUT)
