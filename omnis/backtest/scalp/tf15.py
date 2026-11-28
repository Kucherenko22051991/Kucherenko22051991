import itertools, numpy as np, pandas as pd, bt
def to15(sym):
    df=pd.read_csv(f'{sym}_5m.csv'); df['dt']=pd.to_datetime(df.t,unit='ms')
    r=df.set_index('dt').resample('15min',label='left',closed='left').agg({'t':'first','o':'first','h':'max','l':'min','c':'last','v':'sum','tbv':'sum'}).dropna().reset_index()
    r.to_csv(f'{sym}_15m.csv',index=False)
for s in bt.SYMS: to15(s)
def load15(sym):
    df=pd.read_csv(f'{sym}_15m.csv'); df['dt']=pd.to_datetime(df.dt)
    df['e9']=bt.ema(df.c,9);df['e21']=bt.ema(df.c,21);df['e50']=bt.ema(df.c,50);df['e200']=bt.ema(df.c,200)
    df['rsi']=bt.rsi(df.c);df['atr']=bt.atr(df);m=bt.ema(df.c,12)-bt.ema(df.c,26);df['macdh']=m-bt.ema(m,9)
    day=df.dt.dt.floor('D');tp=(df.h+df.l+df.c)/3
    df['vwap']=(tp*df.v).groupby(day).cumsum()/df.v.groupby(day).cumsum();df['vavg']=df.v.rolling(20).mean();df['buyr']=df.tbv/df.v
    h1=bt.htf(df,'1h');h4=bt.htf(df,'4h')
    for k in ['c','e50','e200','macdh']: df['h1_'+k]=h1[k]
    # for 15m base the "m15" trend filter becomes 1h EMA21/50
    df['m15_e21']=h1['e21'] if 'e21' in h1 else bt.ema(df.c,84); df['m15_e50']=h1['e50']; df['m15_c']=h1['c']
    return df
def evaluate(data, label, minsl_list=[0.0], bars=32):
    cut=data['BTCUSDT'].dt.iloc[int(len(data['BTCUSDT'])*0.6)]
    rows=[]
    for name,fn in bt.STRATS.items():
        keys=list(bt.GRID[name])
        for vals in itertools.product(*[bt.GRID[name][k] for k in keys]):
            p=dict(zip(keys,vals))
            for ex in ['scaled','f2','f1.5']:
                for ms in minsl_list:
                    out=[]
                    for s,df in data.items():
                        L,S,a,b=fn(df,p); t=bt.simulate(df,L,S,a,b,ex,max_bars=bars,min_sl_pct=ms); t['sym']=s; out.append(t)
                    t=pd.concat(out)
                    if len(t)<20: continue
                    i,o=t[t.t<cut],t[t.t>=cut]
                    rows.append(dict(strat=name,exit=ex,minsl=ms,**p,IS_n=len(i),IS_exp=i.R.mean(),IS_ret=i.R.sum()*0.5,OOS_n=len(o),OOS_exp=o.R.mean(),OOS_ret=o.R.sum()*0.5,
                                     OOS_PF=(o.R[o.R>0].sum()/-o.R[o.R<=0].sum()) if (o.R<=0).any() else np.inf, cost=t.cost_R.mean()))
    R=pd.DataFrame(rows)
    print(f'\n##### {label}: variants {len(R)} | IS>0: {(R.IS_exp>0).sum()} | OOS>0: {(R.OOS_exp>0).sum()} | both>0: {((R.IS_exp>0)&(R.OOS_exp>0)).sum()}')
    best=R.sort_values('IS_exp',ascending=False).groupby('strat').head(3)
    print(best[['strat','exit','minsl','IS_n','IS_exp','IS_ret','OOS_n','OOS_exp','OOS_ret','OOS_PF','cost']].round(3).to_string(index=False))
    return R
d5={s:bt.load(s) for s in bt.SYMS}
R5=evaluate(d5,'5m with min-stop filter',[0.003,0.005,0.007],bars=48)
d15={s:load15(s) for s in bt.SYMS}
R15=evaluate(d15,'15m base',[0.0,0.005],bars=32)
R5.to_csv('grid5_minsl.csv',index=False); R15.to_csv('grid15.csv',index=False)
