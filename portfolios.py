"""Live PAPER portfolios: buy_hold and trend (50d SMA). Priced via data-api.binance.vision (keyless). No real trades."""
import json, time, requests, datetime as dt
import run
COINS=["BTCUSDT","ETHUSDT","SOLUSDT"]; FEE=0.002; N=50; START=500.0
API="https://data-api.binance.vision/api/v3"
VERDICT=json.load(open(__file__.rsplit("/",1)[0]+"/backtest/results.json"))["verdict"]
TREND_LABEL="trend (failed backtest)" if VERDICT!="PASS" else "trend"
def prices():
    r=requests.get(API+"/ticker/price",params={"symbols":json.dumps(COINS,separators=(",",":"))},timeout=20).json()
    return {x["symbol"]:float(x["price"]) for x in r}
def daily_signal(sym):
    k=requests.get(API+"/klines",params={"symbol":sym,"interval":"1d","limit":N+5},timeout=20).json()
    now=time.time()*1000; done=[(x[0],float(x[4])) for x in k if x[6]<now]
    closes=[c for _,c in done[-N:]]; day=dt.datetime.fromtimestamp(done[-1][0]/1000,dt.timezone.utc).date().isoformat()
    return day, closes[-1]>sum(closes)/N, closes[-1], sum(closes)/N
def tables(c):
    c.executescript("""create table if not exists pf_pos(pf text, coin text, qty real, cash real, primary key(pf,coin));
    create table if not exists pf_trades(id integer primary key, ts int, pf text, coin text, side text, price real, qty real, fee real, note text);
    create table if not exists pf_snaps(ts int, pf text, equity real, primary key(ts,pf));
    create table if not exists meta(k text primary key, v text);""")
def update(c=None):
    c=c or run.db(); tables(c); px=prices(); now=int(time.time())
    def trade(pf,coin,side,price,note):
        q,cash=c.execute("select qty,cash from pf_pos where pf=? and coin=?",(pf,coin)).fetchone()
        if side=="buy": nq=cash*(1-FEE)/price; fee=cash*FEE; c.execute("update pf_pos set qty=?,cash=0 where pf=? and coin=?",(nq,pf,coin))
        else: v=q*price; fee=v*FEE; nq=q; c.execute("update pf_pos set qty=0,cash=? where pf=? and coin=?",(v-fee,pf,coin))
        c.execute("insert into pf_trades(ts,pf,coin,side,price,qty,fee,note) values(?,?,?,?,?,?,?,?)",(now,pf,coin,side,price,nq,fee,note))
        return fee
    for pf in ("buy_hold","trend"):
        if not c.execute("select 1 from pf_pos where pf=?",(pf,)).fetchone():
            for coin in COINS: c.execute("insert into pf_pos values(?,?,0,?)",(pf,coin,START/3))
            if pf=="buy_hold":
                for coin in COINS: trade(pf,coin,"buy",px[coin],"initial buy-and-hold")
    for coin in COINS:
        day,above,close,sma=daily_signal(coin)
        key=f"trend_day_{coin}"; row=c.execute("select v from meta where k=?",(key,)).fetchone()
        if row and row[0]==day: continue
        c.execute("insert or replace into meta values(?,?)",(key,day))
        q,cash=c.execute("select qty,cash from pf_pos where pf='trend' and coin=?",(coin,)).fetchone()
        side="buy" if above and q==0 else "sell" if not above and q>0 else None
        name=coin.replace("USDT","")
        print(f"  trend {name}: {day} close {close:,.2f} vs SMA50 {sma:,.2f} -> {'above' if above else 'below'}{' => '+side.upper() if side else ''}")
        if side:
            trade("trend",coin,side,px[coin],f"{day} close {close:.2f} {'>' if above else '<='} SMA50 {sma:.2f}")
            try:
                run.notify(f"Trend rule {'bought' if side=='buy' else 'sold'} {name}",
                  f"PAPER trade (no real money). Our '{TREND_LABEL}' portfolio {'bought' if side=='buy' else 'sold'} its ~${START/3:.0f} slice of {name} at ${px[coin]:,.2f}, because yesterday's daily close (${close:,.2f}) was {'above' if above else 'below'} its 50-day average (${sma:,.2f}). Fee 0.2%.")
            except Exception as e: print("notify err",e)
    out={}
    for pf in ("buy_hold","trend"):
        eq=sum(cash+q*px[coin] for coin,q,cash in c.execute("select coin,qty,cash from pf_pos where pf=?",(pf,)).fetchall())
        c.execute("insert or replace into pf_snaps values(?,?,?)",(now,pf,eq)); out[pf]=eq
    c.commit(); print("  portfolios:",{k:round(v,2) for k,v in out.items()}); return out
if __name__=="__main__": update()
