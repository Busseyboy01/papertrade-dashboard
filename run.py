#!/usr/bin/env python3
"""PAPER-TRADING memecoin copy simulator. No keys, no wallets, no real trades. Simulation only."""
import json, re, sqlite3, sys, time, datetime as dt, requests
DB="papertrade.db"; WL="watchlist.json"
RPC="https://api.mainnet-beta.solana.com"
UA={"User-Agent":"Mozilla/5.0"}
BANKROLL=500.0; POS=20.0; SLIP_FEE_EACH=0.0125  # 1.25% per side => ~2.5% round trip
TP=1.0; SL=-0.40; MAX_HOLD=24*3600
MIN_LIQ=20000; MAX_TOP10=0.35; MAX_RC_SCORE=5000; MAX_AGE=6*3600; SIGS_PER_WALLET=15
SOL="So11111111111111111111111111111111111111112"
STABLE={SOL,"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
now=lambda:int(time.time())
import os
STEP=25.0
def notify(title,msg):
    """Push to ntfy. Never raises."""
    try:
        topic=os.environ.get("NTFY_TOPIC") or open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"ntfy_topic.txt")).read().strip()
        requests.post("https://ntfy.sh/"+topic,data=msg.encode(),headers={"Title":"PAPER: "+title},timeout=10)
    except Exception as e: print(f"  (notify failed: {e})")
def equity(c,px):
    v=0
    for mint,qty in c.execute("select mint,qty from positions where status='open'").fetchall():
        p=px.get(mint)
        if p: v+=qty*float(p["priceUsd"])*(1-SLIP_FEE_EACH)
    return cash(c)+v
def db():
    c=sqlite3.connect(DB); c.executescript("""
    create table if not exists meta(k text primary key, v text);
    create table if not exists seen(sig text primary key, wallet text, ts int);
    create table if not exists signals(id integer primary key, ts int, wallet text, sig text, mint text, side text, symbol text, decision text, reason text, checks text);
    create table if not exists positions(id integer primary key, mint text, symbol text, wallet text, open_ts int, entry_px real, qty real, cost real,
      status text default 'open', close_ts int, exit_px real, proceeds real, pnl real, exit_reason text);"""); return c
def rpc(m,p):
    for i in range(5):
        r=requests.post(RPC,json={"jsonrpc":"2.0","id":1,"method":m,"params":p},timeout=20)
        if r.status_code==429: time.sleep(2*(i+1)); continue
        j=r.json()
        if "error" in j: raise RuntimeError(j["error"])
        return j["result"]
    raise RuntimeError("RPC rate limited")
def dex(mints):
    out={}
    for i in range(0,len(mints),30):
        r=requests.get("https://api.dexscreener.com/latest/dex/tokens/"+",".join(mints[i:i+30]),timeout=20).json()
        for p in r.get("pairs") or []:
            if p.get("chainId")!="solana": continue
            m=p["baseToken"]["address"]
            if m not in out or (p.get("liquidity") or {}).get("usd",0)>(out[m].get("liquidity") or {}).get("usd",0): out[m]=p
    return out
def rugcheck(mint):
    try:
        r=requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report",timeout=25)
        return r.json() if r.ok else None
    except Exception: return None
def init():
    """Seed watchlist from kolscan.io public 1-day leaderboard (public HTML)."""
    h=requests.get("https://kolscan.io/leaderboard",headers=UA,timeout=20).text
    seen=[];wl=[]
    for m in re.finditer(r'/account/([1-9A-HJ-NP-Za-km-z]{32,44})',h):
        a=m.group(1)
        if a in seen: continue
        seen.append(a); i=m.start(); t=" ".join(re.sub(r'<[^>]+>',' ',h[i:i+1500]).split())
        mm=re.search(r'"> (.+?) \S+ (\d+) / (\d+) ([+-][\d.,]+) Sol \( \$([\d,.]+) \)',t)
        if not mm: continue
        w,l=int(mm.group(2)),int(mm.group(3))
        if w+l<3: continue  # skip wallets with too few trades to judge
        wl.append({"wallet":a,"name":mm.group(1),"source":"https://kolscan.io/leaderboard (1d tab, fetched %s)"%dt.datetime.now().isoformat(timespec='seconds'),
                   "kolscan_rank":len(seen),"wins_losses":f"{w}/{l}","pnl_sol_1d":mm.group(4),"pnl_usd_1d":mm.group(5)})
        if len(wl)>=8: break
    json.dump(wl,open(WL,"w"),indent=1); print(f"watchlist: {len(wl)} wallets written to {WL}")
    for w in wl: print(f"  #{w['kolscan_rank']} {w['name']:<14} {w['wallet']} W/L {w['wins_losses']} PnL {w['pnl_sol_1d']} SOL (${w['pnl_usd_1d']})")
def swaps(wallet,c):
    """Return list of (sig,ts,mint,side) from recent txs via token balance deltas owned by wallet."""
    out=[]
    for s in rpc("getSignaturesForAddress",[wallet,{"limit":SIGS_PER_WALLET}]):
        sig=s["signature"]
        if s.get("err") or c.execute("select 1 from seen where sig=?",(sig,)).fetchone(): continue
        if now()-(s.get("blockTime") or 0)>MAX_AGE:
            c.execute("insert into seen values(?,?,?)",(sig,wallet,s.get("blockTime") or 0)); continue
        time.sleep(0.4)
        tx=rpc("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":1}])
        c.execute("insert into seen values(?,?,?)",(sig,wallet,s.get("blockTime") or 0))
        if not tx: continue
        m=tx["meta"]; d={}
        for k,sgn in (("preTokenBalances",-1),("postTokenBalances",1)):
            for b in m.get(k) or []:
                if b.get("owner")==wallet: d[b["mint"]]=d.get(b["mint"],0)+sgn*float(b["uiTokenAmount"]["uiAmount"] or 0)
        for mint,dv in d.items():
            if mint in STABLE or abs(dv)<1e-9: continue
            out.insert(0,(sig,s.get("blockTime"),mint,"buy" if dv>0 else "sell"))
    return out
def safety(mint,p):
    chk={}; bad=[]
    if not p: return False,"no DexScreener pair (no price/liquidity)",chk
    liq=(p.get("liquidity") or {}).get("usd",0) or 0; chk["liq_usd"]=liq; chk["price"]=float(p.get("priceUsd") or 0)
    if liq<MIN_LIQ: bad.append(f"liquidity ${liq:,.0f} < ${MIN_LIQ:,}")
    rc=rugcheck(mint)
    if rc is None: bad.append("rugcheck unavailable")
    else:
        chk["mint_auth"]=rc.get("mintAuthority"); chk["freeze_auth"]=rc.get("freezeAuthority")
        chk["rc_score"]=rc.get("score"); chk["rugged"]=rc.get("rugged")
        hs=rc.get("topHolders") or []
        known=set(rc.get("knownAccounts") or {})  # LP/AMM vaults excluded from concentration
        nonamm=[h for h in hs if h.get("owner") not in known][:10]; chk["top10_ex_known"]=round(sum(h.get("pct") or 0 for h in nonamm)/100,3)
        if rc.get("rugged"): bad.append("rugcheck: rugged")
        if rc.get("mintAuthority"): bad.append("mint authority enabled")
        if rc.get("freezeAuthority"): bad.append("freeze authority enabled")
        if (rc.get("score") or 0)>MAX_RC_SCORE: bad.append(f"rugcheck score {rc.get('score')} > {MAX_RC_SCORE}")
        if chk["top10_ex_known"]>MAX_TOP10: bad.append(f"top10 holders {chk['top10_ex_known']:.0%} > {MAX_TOP10:.0%}")
        dang=[r["name"] for r in rc.get("risks") or [] if r.get("level")=="danger"]
        if dang: chk["danger"]=dang
    return (not bad),("; ".join(bad) or "passed safety"),chk
def cash(c):
    spent=c.execute("select coalesce(sum(cost),0) from positions").fetchone()[0]
    back=c.execute("select coalesce(sum(proceeds),0) from positions where status='closed'").fetchone()[0]
    return BANKROLL-spent+back
def close(c,pos,px,why,allpx=None):
    pid,qty,cost=pos
    proceeds=qty*px*(1-SLIP_FEE_EACH)
    c.execute("update positions set status='closed',close_ts=?,exit_px=?,proceeds=?,pnl=?,exit_reason=? where id=?",(now(),px,proceeds,proceeds-cost,why,pid))
    try:
        sym=c.execute("select symbol from positions where id=?",(pid,)).fetchone()[0]
        eq=equity(c,allpx or {}); pct=(proceeds-cost)/cost*100
        plain={"mirrored trader sell":"the trader we copied sold","take profit +100%":"it doubled (take-profit)","stop loss -40%":"it fell 40% (stop-loss)","max hold 24h":"we held it 24 hours (time limit)"}.get(why,why)
        notify(f"Sold {sym} {'+' if pct>=0 else '-'}${abs(proceeds-cost):.2f}",
          f"PAPER trade (no real money). Sold our pretend $20 of {sym} because {plain}. "
          f"Result: {'gain' if pct>=0 else 'loss'} of ${abs(proceeds-cost):.2f} ({pct:+.1f}%). Pretend account now worth ${eq:.2f} (started at $500).")
    except Exception as e: print(f"  (notify error {e})")
    print(f"  CLOSE #{pid} @ {px:.8g} proceeds ${proceeds:.2f} pnl ${proceeds-cost:+.2f} ({why})")
def poll():
    c=db(); wl=json.load(open(WL)); events=[]
    for w in wl:
        try: ev=swaps(w["wallet"],c); events+= [(w["wallet"],)+e for e in ev]; print(f"{w['name']:<14} {len(ev)} new token swaps")
        except Exception as e: print(f"{w['name']:<14} ERROR {e}")
        c.commit(); time.sleep(0.5)
    mints=list({e[3] for e in events}|{r[0] for r in c.execute("select mint from positions where status='open'")})
    px=dex(mints) if mints else {}
    for wallet,sig,ts,mint,side in events:
        p=px.get(mint); sym=(p or {}).get("baseToken",{}).get("symbol","?")
        if side=="sell":
            for pos in c.execute("select id,qty,cost from positions where status='open' and mint=? and wallet=?",(mint,wallet)).fetchall():
                if p: close(c,pos,float(p["priceUsd"]),"mirrored trader sell",px)
            c.execute("insert into signals(ts,wallet,sig,mint,side,symbol,decision,reason,checks) values(?,?,?,?,?,?,?,?,?)",(now(),wallet,sig,mint,side,sym,"-","trader sell",""))
            continue
        if c.execute("select 1 from positions where mint=? and status='open'",(mint,)).fetchone(): dec,why,chk="SKIP","already holding",{}
        else:
            ok,why,chk=safety(mint,p)
            dec="COPY" if ok else "SKIP"
            if ok and cash(c)<POS: dec,why="SKIP","insufficient virtual cash"
        print(f"  {dec} {sym} {mint[:8]}.. by {wallet[:6]}: {why}")
        c.execute("insert into signals(ts,wallet,sig,mint,side,symbol,decision,reason,checks) values(?,?,?,?,?,?,?,?,?)",(now(),wallet,sig,mint,side,sym,dec,why,json.dumps(chk,default=str)))
        if dec=="COPY":
            fill=float(p["priceUsd"])*(1+SLIP_FEE_EACH)
            c.execute("insert into positions(mint,symbol,wallet,open_ts,entry_px,qty,cost) values(?,?,?,?,?,?,?)",(mint,sym,wallet,now(),fill,POS/fill,POS))
            try:
                tr=next((w["name"] for w in wl if w["wallet"]==wallet),wallet[:6])
                notify(f"Copied {sym}",f"PAPER trade (no real money). Trader '{tr}' just bought {sym}, it passed our safety checks, so we pretend-bought ${POS:.0f} worth at ${fill:.8g} per token (price incl. fees). Token: {mint}")
            except Exception as e: print(f"  (notify error {e})")
            print(f"    OPEN ${POS} {sym} @ {fill:.8g} (mkt {p['priceUsd']} +{SLIP_FEE_EACH:.2%} slip/fee)")
    for pid,mint,ets,epx,qty,cost in c.execute("select id,mint,open_ts,entry_px,qty,cost from positions where status='open'").fetchall():
        p=px.get(mint)
        if not p: continue
        cur=float(p["priceUsd"])*(1-SLIP_FEE_EACH); r=cur/epx-1
        if r>=TP: close(c,(pid,qty,cost),float(p["priceUsd"]),"take profit +100%",px)
        elif r<=SL: close(c,(pid,qty,cost),float(p["priceUsd"]),"stop loss -40%",px)
        elif now()-ets>MAX_HOLD: close(c,(pid,qty,cost),float(p["priceUsd"]),"max hold 24h",px)
    try:
        eq=equity(c,dex(list({r[0] for r in c.execute("select mint from positions where status='open'")})) or {}) if c.execute("select 1 from positions where status='open'").fetchone() else cash(c)
        step=int((eq-BANKROLL)/STEP)  # 0 = within $25 of $500
        row=c.execute("select v from meta where k='last_step'").fetchone(); last=int(row[0]) if row else 0
        if step!=last:
            c.execute("insert or replace into meta values('last_step',?)",(str(step),))
            lvl=BANKROLL+step*STEP; up=step>last
            notify(f"Account {'up' if up else 'down'} to ~${lvl:.0f}",
              f"PAPER account (no real money) is now worth ${eq:.2f}, {'above' if eq>=BANKROLL else 'below'} the $500 we started with by ${abs(eq-BANKROLL):.2f}. It just {'rose past' if up else 'dropped past'} the ${BANKROLL+(step if abs(step)>abs(last) else last)*STEP:.0f} mark.")
    except Exception as e: print(f"  (equity notify error {e})")
    c.commit(); report(c)
    try:
        import portfolios; portfolios.update(c)
    except Exception as e: print(f"(portfolios error: {e})")
    try:
        import dashboard; dashboard.build()
        if not os.environ.get("GITHUB_ACTIONS"):
            import publish; publish.publish()
    except Exception as e: print(f"(dashboard error: {e})")
def report(c=None):
    c=c or db(); mints=[r[0] for r in c.execute("select mint from positions where status='open'")]
    px=dex(mints) if mints else {}; unreal=0; val=0
    print("\n=== PAPER P&L (simulated, no real trades) ===")
    for pid,sym,mint,epx,qty,cost in c.execute("select id,symbol,mint,entry_px,qty,cost from positions where status='open'"):
        p=px.get(mint); v=qty*float(p["priceUsd"])*(1-SLIP_FEE_EACH) if p else 0; val+=v; unreal+=v-cost
        print(f" open #{pid} {sym:<10} cost ${cost:.2f} mark ${v:.2f} ({v-cost:+.2f})")
    real=c.execute("select coalesce(sum(pnl),0),count(*),sum(pnl>0) from positions where status='closed'").fetchone()
    sk=dict(c.execute("select decision,count(*) from signals group by decision").fetchall())
    cs=cash(c)
    print(f" signals: {sk}\n closed trades: {real[1]} (wins {real[2] or 0}) realized ${real[0]:+.2f}\n unrealized ${unreal:+.2f}")
    print(f" cash ${cs:.2f} + open ${val:.2f} = equity ${cs+val:.2f} (start ${BANKROLL:.0f})")
if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "report"
    {"init":init,"poll":poll,"report":report}[cmd]()
