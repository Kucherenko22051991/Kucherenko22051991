import json,sys,glob,os
import pandas as pd
T=sys.argv[1]; out=sys.argv[2]
files={'BTCUSDT':['eu2s9k','hbk18f','siaetw','z5zza8','n0os38','42u4ks'],
 'ETHUSDT':['px0dij','fgyvgo','1fqcz7','9cudna','1z8y19','jok8lo'],
 'SOLUSDT':['8bfsm2','166vdz','26bs4v','tt13cx','1o5fzz','xvnkb7']}
for sym,ids in files.items():
    rows=[]
    for i in ids:
        f=glob.glob(f"{T}/mcp-Binance-futures_usds_klineCandlestickData-*-{i}.txt")[0]
        raw=open(f).read().strip()
        d=json.loads(raw)
        if isinstance(d,dict): d=d.get('data',d)
        rows+=d
    df=pd.DataFrame(rows).iloc[:,:11]
    df.columns=['t','o','h','l','c','v','ct','qv','n','tbv','tbqv']
    df=df.astype(float).drop_duplicates('t').sort_values('t')
    df=df[df.ct< 1795637400000]  # drop unfinished bar
    gaps=(df.t.diff().dropna()!=300000).sum()
    print(sym,len(df),'gaps',gaps,pd.to_datetime(df.t.iloc[0],unit='ms'),'->',pd.to_datetime(df.t.iloc[-1],unit='ms'),'first close',df.c.iloc[0],'last',df.c.iloc[-1])
    df.to_csv(f"{out}/{sym}_5m.csv",index=False)
