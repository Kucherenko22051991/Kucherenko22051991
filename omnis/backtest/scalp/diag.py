import itertools, numpy as np, pandas as pd, bt
data={s:bt.load(s) for s in bt.SYMS}
cut=data['BTCUSDT'].dt.iloc[int(9000*0.6)]
def run(fn,p,ex,data,min_sl=0.0):
    out=[]
    for s,df in data.items():
        L,S,a,b=fn(df,p); t=bt.simulate(df,L,S,a,b,ex,min_sl_pct=min_sl); t['sym']=s; out.append(t)
    return pd.concat(out)
# 1) gross edge (no costs) across whole grid
rows=[]
for name,fn in bt.STRATS.items():
    keys=list(bt.GRID[name])
    for vals in itertools.product(*[bt.GRID[name][k] for k in keys]):
        p=dict(zip(keys,vals))
        for ex in ['scaled','f2']:
            t=run(fn,p,ex,data)
            if len(t)==0: continue
            g=t.R_gross; c=t.cost_R
            # maker entry (0.02%) + taker exit (0.05%) + slip 0.02% on exit only
            mk=(0.0002+0.0005+0.0002)/bt.RT*c
            rows.append(dict(s=name,ex=ex,n=len(t),gross=g.mean(),taker=(g-c).mean(),maker=(g-mk).mean(),
                IS_gross=t[t.t<cut].R_gross.mean(),OOS_gross=t[t.t>=cut].R_gross.mean()))
R=pd.DataFrame(rows)
print(R.groupby('s')[['n','gross','IS_gross','OOS_gross','taker','maker']].agg(['mean','max']).round(3).to_string())
print('variants with gross>0 in BOTH halves:',((R.IS_gross>0)&(R.OOS_gross>0)).groupby(R.s).sum().to_dict())
