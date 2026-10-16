import json, numpy as np, pandas as pd, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
matplotlib.rcParams.update({'pdf.fonttype':42,'font.family':['Liberation Sans','DejaVu Sans'],'font.size':6.5,'axes.linewidth':0.6})
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.svm import OneClassSVM
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_curve, roc_auc_score, confusion_matrix, precision_recall_fscore_support
import train_lstm_reproducible as T
df,mu,sd=T.load('data/azure_public/cpu_stress_eastus.csv')
tr,te=df[df.VM_id==0],df[df.VM_id==1]
sc=StandardScaler().fit(tr[T.FEATS].values)   # repo script scales the same way (fit on VM0)
Xtr,Xte=sc.transform(tr[T.FEATS].values),sc.transform(te[T.FEATS].values); ytr,yte=tr.y.values,te.y.values
models={}
ocs=OneClassSVM(kernel='rbf',nu=0.05,gamma='scale').fit(Xtr); models['One-Class SVM']=(-ocs.decision_function(Xte),(ocs.predict(Xte)==-1).astype(int))
rf=RandomForestClassifier(n_estimators=200,class_weight='balanced',random_state=42,n_jobs=-1).fit(Xtr,ytr); models['Random Forest']=(rf.predict_proba(Xte)[:,1],rf.predict(Xte))
iso=IsolationForest(n_estimators=200,contamination=0.05,random_state=42,n_jobs=-1).fit(Xtr); models['Isolation Forest']=(-iso.score_samples(Xte),(iso.predict(Xte)==-1).astype(int))
out={}
for k,(s,p) in models.items():
    pr,rc,f1,_=precision_recall_fscore_support(yte,p,average='binary',zero_division=0); tn,fp,fn,tp=confusion_matrix(yte,p,labels=[0,1]).ravel()
    out[k]=dict(P=round(pr,3),R=round(rc,3),F1=round(f1,3),AUC=round(roc_auc_score(yte,s),3),tn=int(tn),fp=int(fp),fn=int(fn),tp=int(tp)); print(k,out[k])
R=json.load(open('results/lstm_results.json')); r0=R['runs'][0]; C=np.load('results/curves_seed0.npz')
out['LSTM']=dict(tn=r0['tn'],fp=r0['fp'],fn=r0['fn'],tp=r0['tp'],AUC=round(r0['roc_auc'],3)); json.dump(out,open('results/confusion_counts.json','w'),indent=1)
BL,GR,OR,RD,GY='#4c78a8','#59a14f','#f28e2b','#e15759','#888888'
# ---- Fig 8: confusion matrices (real counts) ----
fig,axs=plt.subplots(1,4,figsize=(7.16,1.85))
order=[('One-Class SVM',out['One-Class SVM']),('Random Forest',out['Random Forest']),('Isolation Forest',out['Isolation Forest']),('LSTM autoencoder (seed 0)',out['LSTM'])]
for ax,(name,o) in zip(axs,order):
    M=np.array([[o['tn'],o['fp']],[o['fn'],o['tp']]]); ax.imshow(np.log10(M+1),cmap='Blues',vmin=0,vmax=3.6)
    for i in range(2):
        for j in range(2): ax.text(j,i,f'{M[i,j]:,}',ha='center',va='center',fontsize=8,fontweight='bold',color='white' if np.log10(M[i,j]+1)>2.2 else 'black')
    ax.set_xticks([0,1]); ax.set_xticklabels(['Normal','Anomaly']); ax.set_yticks([0,1]); ax.set_yticklabels(['Normal','Anomaly'],rotation=90,va='center')
    ax.set_xlabel('Predicted'); ax.set_ylabel('Actual'); ax.set_title(name,fontsize=7,fontweight='bold')
fig.tight_layout(); fig.savefig('fig8_confusion_matrices.pdf'); plt.close(fig)
# ---- Fig 5: ROC + LSTM error distribution (real) ----
fig,(a1,a2)=plt.subplots(1,2,figsize=(3.45,1.75),gridspec_kw={'width_ratios':[1,1.05]})
for name,col in [('One-Class SVM',GY),('Isolation Forest',BL),('Random Forest',GR)]:
    s=models[name][0]; f,t,_=roc_curve(yte,s); a1.plot(f,t,color=col,lw=1,label=f'{name.split()[0][:3] if False else name} ({roc_auc_score(yte,s):.2f})')
a1.plot(C['fpr'],C['tpr'],color=OR,lw=1,label=f"LSTM AE ({r0['roc_auc']:.2f})"); a1.plot([0,1],[0,1],'k--',lw=.6)
a1.set_xlabel('False positive rate'); a1.set_ylabel('True positive rate'); a1.legend(fontsize=4.8,loc='lower right',frameon=False); a1.set_title('ROC (validation VM)',fontsize=6.5)
en,ea=C['err_normal'],C['err_anom']; bins=np.linspace(0,max(en.max(),ea.max()),50)
a2.hist(en,bins=bins,color=BL,alpha=.8,label=f'Normal (n={len(en):,})',density=True); a2.hist(ea,bins=bins,color=RD,alpha=.9,label=f'Anomaly (n={len(ea)})',density=True)
a2.axvline(float(C['thr']),color='k',ls='--',lw=.8,label=r'$\tau$ (95th pct, calibration)'); a2.set_xlabel('Reconstruction error (MSE)'); a2.set_ylabel('Density'); a2.legend(fontsize=4.8,frameon=False); a2.set_title('LSTM error distribution',fontsize=6.5)
fig.tight_layout(pad=0.4); fig.savefig('fig5_roc_distribution.pdf'); plt.close(fig)
# ---- Fig 6: training curve (real) ----
fig,ax=plt.subplots(figsize=(3.45,1.7)); ep=np.arange(1,len(C['loss'])+1)
ax.plot(ep,C['loss'],color=BL,lw=1,label='Train MSE'); ax.plot(ep,C['val_loss'],color=OR,lw=1,ls='--',label='Validation MSE')
ax.axvline(r0['best_epoch'],color=RD,ls=':',lw=.9,label=f"Best epoch ({r0['best_epoch']})"); ax.set_xlabel('Epoch'); ax.set_ylabel('MSE'); ax.legend(fontsize=5.5,frameon=False)
fig.tight_layout(pad=0.4); fig.savefig('fig6_lstm_training.pdf'); plt.close(fig)
for f in ('fig5_roc_distribution','fig6_lstm_training','fig8_confusion_matrices'):
    import subprocess; subprocess.run(['pdftoppm','-r','170','-png','-singlefile',f+'.pdf',f])
print('done')
