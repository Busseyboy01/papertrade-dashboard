import requests, json, math, datetime as dt, sys
COINS=["BTCUSDT","ETHUSDT","SOLUSDT"]; FEE=0.002; DAYS=730
def kl(sym):
    r=requests.get("https://data-api.binance.vision/api/v3/klines",params={"symbol":sym,"interval":"1d","limit":1000},timeout=30).json()
    now=dt.datetime.now(dt.timezone.utc).timestamp()*1000
    return [(k[0],float(k[4])) for k in r if k[6]<now]  # complete days only
def run(data,n,trend=True):
    eq_curve=[]; trades=0; slices={}
    for s,cl in data.items():
        closes=[c for _,c in cl]; start=len(closes)-DAYS
        cash=500/3; qty=0; curve=[]
        for i in range(start,len(closes)):
            c=closes[i]; sma=sum(closes[i-n+1:i+1])/n
            want=(c>sma) if trend else True
            if want and qty==0 and (trend or i==start): qty=cash*(1-FEE)/c; cash=0; trades+=1
            elif trend and not want and qty>0: cash=qty*c*(1-FEE); qty=0; trades+=1
            curve.append(cash+qty*c)
        slices[s]=curve
    tot=[sum(v) for v in zip(*slices.values())]
    peak=0; mdd=0
    for v in tot: peak=max(peak,v); mdd=min(mdd,v/peak-1)
    ret=tot[-1]/500-1; yrs=(len(tot)-1)/365; cagr=(tot[-1]/500)**(1/yrs)-1
    return dict(final=round(tot[-1],2),total_return=ret,cagr=cagr,max_dd=mdd,trades=trades)
data={s:kl(s) for s in COINS}
first=dt.datetime.utcfromtimestamp(data["BTCUSDT"][-DAYS][0]/1000).date(); last=dt.datetime.utcfromtimestamp(data["BTCUSDT"][-1][0]/1000).date()
bh=run(data,50,False); tr=run(data,50)
c1=tr["total_return"]>bh["total_return"] or (abs(tr["max_dd"])<=abs(bh["max_dd"])*2/3 and (tr["total_return"]>=0.7*bh["total_return"] if bh["total_return"]>0 else tr["total_return"]>=bh["total_return"]))
c2=tr["max_dd"]>=-0.25
res=dict(window=f"{first}..{last} UTC ({DAYS} daily closes)",buy_hold=bh,trend50=tr,crit1=c1,crit2=c2,verdict="PASS" if c1 and c2 else "FAIL",
         robustness={"trend20":run(data,20),"trend100":run(data,100)})
json.dump(res,open("backtest/results.json","w"),indent=1,default=str)
f=lambda d:f"final ${d['final']:.2f} ret {d['total_return']:+.1%} CAGR {d['cagr']:+.1%} maxDD {d['max_dd']:.1%} trades {d['trades']}"
print(res["window"]);print("B&H     ",f(bh));print("Trend50 ",f(tr));print("crit1",c1,"crit2",c2,"=>",res["verdict"])
for k,v in res["robustness"].items(): print("robust",k,f(v))
