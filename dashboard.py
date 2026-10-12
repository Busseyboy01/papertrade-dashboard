#!/usr/bin/env python3
"""Writes dashboard/index.html (static, self-contained) from papertrade.db. PAPER TRADING only."""
import json, os, sqlite3, time, datetime as dt, html, collections
from zoneinfo import ZoneInfo
import run
D=os.path.dirname(os.path.abspath(__file__)); PT=ZoneInfo("America/Vancouver")
REASON={"mirrored trader sell":"The trader we copied sold","take profit +100%":"Doubled - took profit","stop loss -40%":"Fell 40% - stop-loss","max hold 24h":"Held 24h - time limit"}
def fmt_dur(s):
    s=int(s); return f"{s//3600}h {s%3600//60}m" if s>=3600 else f"{s//60}m"
def money(x): return ("+" if x>=0 else "-")+f"${abs(x):,.2f}"
def build():
    os.chdir(D); c=run.db()
    c.execute("create table if not exists equity_snaps(ts int primary key, equity real, cash real)")
    names={w["wallet"]:w["name"] for w in json.load(open(run.WL))}
    opens=c.execute("select id,symbol,mint,wallet,open_ts,entry_px,qty,cost from positions where status='open'").fetchall()
    px=run.dex([o[2] for o in opens]) if opens else {}
    cash=run.cash(c); eq=run.equity(c,px); now=int(time.time())
    c.execute("insert or replace into equity_snaps values(?,?,?)",(now,eq,cash)); c.commit()
    snaps=c.execute("select ts,equity from equity_snaps order by ts").fetchall()
    # chart
    W,H=340,150; ys=[s[1] for s in snaps]+[run.BANKROLL]; lo,hi=min(ys)-1,max(ys)+1
    t0,t1=snaps[0][0],max(snaps[-1][0],snaps[0][0]+1)
    X=lambda t:8+(t-t0)/(t1-t0)*(W-16); Y=lambda v:H-8-(v-lo)/(hi-lo)*(H-16)
    pts=" ".join(f"{X(t):.1f},{Y(v):.1f}" for t,v in snaps)
    col="#22c55e" if eq>=run.BANKROLL else "#ef4444"
    svg=f'''<svg viewBox="0 0 {W} {H}" width="100%"><line x1="0" x2="{W}" y1="{Y(run.BANKROLL):.1f}" y2="{Y(run.BANKROLL):.1f}" stroke="#555" stroke-dasharray="4"/>
<text x="4" y="{Y(run.BANKROLL)-3:.1f}" fill="#888" font-size="10">$500 start</text><polyline fill="none" stroke="{col}" stroke-width="2" points="{pts}"/>
<text x="4" y="12" fill="#888" font-size="10">${hi:.0f}</text><text x="4" y="{H-2}" fill="#888" font-size="10">${lo:.0f}</text></svg>
<div class=sub>{len(snaps)} snapshots since {dt.datetime.fromtimestamp(t0,PT):%b %d %H:%M} PT</div>'''
    rows=""
    for pid,sym,mint,w,ots,epx,qty,cost in opens:
        p=px.get(mint); cur=float(p["priceUsd"]) if p else 0; val=qty*cur*(1-run.SLIP_FEE_EACH); pl=val-cost
        rows+=f'<tr><td><a href="https://dexscreener.com/solana/{mint}">{html.escape(sym)}</a><div class=sub>{html.escape(names.get(w,w[:6]))}</div></td><td>${epx:.6g}<div class=sub>${cur:.6g}</div></td><td class={"g" if pl>=0 else "r"}>{money(pl)}<div class=sub>{pl/cost*100:+.1f}%</div></td><td>{fmt_dur(now-ots)}</td></tr>'
    crow=""
    for sym,w,ots,cts,why,pnl,cost in c.execute("select symbol,wallet,open_ts,close_ts,exit_reason,pnl,cost from positions where status='closed' order by close_ts desc limit 30"):
        crow+=f'<tr><td>{html.escape(sym)}<div class=sub>{html.escape(names.get(w,w[:6]))}</div></td><td>{REASON.get(why,why)}<div class=sub>{dt.datetime.fromtimestamp(cts,PT):%b %d %H:%M}</div></td><td class={"g" if pnl>=0 else "r"}>{money(pnl)}<div class=sub>{pnl/cost*100:+.1f}%</div></td></tr>'
    midnight=int(dt.datetime.now(PT).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
    cnt=dict(c.execute("select decision,count(*) from signals where ts>=? group by decision",(midnight,)).fetchall())
    rc=collections.Counter()
    for (r,) in c.execute("select reason from signals where decision='SKIP' and ts>=?",(midnight,)):
        for part in r.split("; "):
            k=part.split(" $")[0].split(" <")[0].split(" >")[0].split(" score")[0].split(" holders")[0]
            rc[{"liquidity":"Too little liquidity (<$20k)","top10":"Top-10 holders own too much","rugcheck":"Bad rugcheck score","already holding":"Already holding it"}.get(k,k)]+=1
    skips="".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>" for k,v in rc.most_common(6))
    diff=eq-run.BANKROLL
    # --- comparison of all three PAPER portfolios ---
    cmp_html=""
    try:
        import portfolios as P
        series={"memecoin copy":snaps}
        for pf,label in (("buy_hold","buy & hold BTC/ETH/SOL"),("trend",P.TREND_LABEL)):
            series[label]=c.execute("select ts,equity from pf_snaps where pf=? order by ts",(pf,)).fetchall()
        colors=["#f59e0b","#60a5fa","#c084fc"]
        allp=[p for v in series.values() for p in v]
        if allp:
            T0=min(t for t,_ in allp); T1=max(max(t for t,_ in allp),T0+1); V=[v for _,v in allp]+[500]; L,Hh=min(V)-1,max(V)+1
            X2=lambda t:8+(t-T0)/(T1-T0)*(W-16); Y2=lambda v:H-8-(v-L)/(Hh-L)*(H-16)
            lines="".join(f'<polyline fill="none" stroke="{colors[i]}" stroke-width="2" points="{" ".join(f"{X2(t):.1f},{Y2(v):.1f}" for t,v in pts)}"/>' + (f'<circle cx="{X2(pts[0][0]):.1f}" cy="{Y2(pts[0][1]):.1f}" r="2.5" fill="{colors[i]}"/>' if len(pts)==1 else "") for i,pts in enumerate(series.values()) if pts)
            csvg=f'<svg viewBox="0 0 {W} {H}" width="100%"><line x1="0" x2="{W}" y1="{Y2(500):.1f}" y2="{Y2(500):.1f}" stroke="#555" stroke-dasharray="4"/><text x="4" y="{Y2(500)-3:.1f}" fill="#888" font-size="10">$500</text><text x="4" y="12" fill="#888" font-size="10">${Hh:.0f}</text><text x="4" y="{H-2}" fill="#888" font-size="10">${L:.0f}</text>{lines}</svg>'
            rows2=""
            for i,(lab,pts) in enumerate(series.items()):
                if not pts: continue
                e=pts[-1][1]; d=e-500
                rows2+=f'<tr><td><span style="color:{colors[i]}">&#9632;</span> {html.escape(lab)}</td><td>${e:,.2f}</td><td class={"g" if d>=0 else "r"}>{d/5:+.2f}%</td></tr>'
            cmp_html=f'<div class=card><h2>Three PAPER strategies, each started with $500</h2><table>{rows2}</table>{csvg}<div class=sub>Memecoin since {dt.datetime.fromtimestamp(snaps[0][0],PT):%b %d %H:%M} PT; BTC/ETH/SOL portfolios since {dt.datetime.fromtimestamp(min(t for t,_ in allp if True),PT):%b %d}. Trend rule = hold each coin only while its daily close is above its 50-day average; it FAILED its pre-set 2-year backtest (max drawdown -32.5% vs -25% limit).</div></div>'
    except Exception as e: cmp_html=f"<div class=sub>(comparison unavailable: {html.escape(str(e))})</div>"
    page=f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><meta http-equiv="Pragma" content="no-cache"><meta http-equiv="Expires" content="0"><meta name=apple-mobile-web-app-capable content=yes><meta name=mobile-web-app-capable content=yes>
<meta name=apple-mobile-web-app-status-bar-style content=black-translucent><meta name=apple-mobile-web-app-title content="PaperTrade"><meta name=theme-color content="#0b0f14">
<link rel=apple-touch-icon href="icon.png"><link rel=icon href="icon.png"><title>PAPER ${eq:.2f}</title>
<style>body{{background:#0b0f14;color:#e5e7eb;font:16px -apple-system,system-ui,sans-serif;margin:0;padding:env(safe-area-inset-top) 12px 30px}}
.banner{{background:#7c2d12;color:#fed7aa;text-align:center;font-weight:700;padding:8px;border-radius:8px;margin:10px 0}}
.card{{background:#151b23;border-radius:12px;padding:12px;margin:10px 0}}h2{{font-size:15px;color:#9ca3af;margin:0 0 8px}}
.big{{font-size:44px;font-weight:800}}.g{{color:#22c55e}}.r{{color:#ef4444}}.sub{{font-size:12px;color:#8b949e}}
table{{width:100%;border-collapse:collapse;font-size:14px}}td{{padding:6px 4px;border-top:1px solid #222a35;vertical-align:top}}a{{color:#93c5fd;text-decoration:none}}
.row{{display:flex;gap:10px}}.row .card{{flex:1;text-align:center}}</style></head><body>
<div class=banner>PAPER TRADING - no real money</div>
<div class=card style="display:flex;align-items:center;justify-content:space-between;gap:8px"><div><div style="font-size:18px;font-weight:700">Data as of {dt.datetime.now(PT):%I:%M %p} PT</div><div class=sub>{dt.datetime.now(PT):%a %b %d} · data updates about every 5 minutes</div></div>
<button onclick="location.href=location.pathname+'?t='+Date.now()" style="background:#2563eb;color:#fff;border:0;border-radius:10px;padding:12px 18px;font-size:16px;font-weight:700">Refresh</button></div>
<script>setTimeout(function(){{location.href=location.pathname+'?t='+Date.now()}},60000)</script>
{cmp_html}<h2 style=\"margin-top:18px\">Memecoin copy bot detail</h2><div class=card><h2>Pretend account value</h2><div class="big {"g" if diff>=0 else "r"}">${eq:,.2f}</div>
<div class={"g" if diff>=0 else "r"}>{money(diff)} ({diff/run.BANKROLL*100:+.2f}%) vs $500 start</div><div class=sub>Cash ${cash:,.2f} · In positions ${eq-cash:,.2f}</div></div>
<div class=row><div class=card><h2>Copied today</h2><div class=big style="font-size:30px">{cnt.get("COPY",0)}</div></div><div class=card><h2>Skipped today</h2><div class=big style="font-size:30px">{cnt.get("SKIP",0)}</div></div></div>
<div class=card><h2>Account value over time</h2>{svg}</div>
<div class=card><h2>Open positions ({len(opens)})</h2><table><tr class=sub><td>Token / trader</td><td>Entry / now</td><td>P&amp;L</td><td>Held</td></tr>{rows or "<tr><td>None</td></tr>"}</table></div>
<div class=card><h2>Closed trades</h2><table>{crow or "<tr><td class=sub>None yet</td></tr>"}</table></div>
<div class=card><h2>Top skip reasons today</h2><table>{skips or "<tr><td class=sub>None</td></tr>"}</table></div>
<div class=sub style="text-align:center">Updated {dt.datetime.now(PT):%a %b %d, %I:%M %p} PT · refreshes every 60s<br>Simulated $20 positions, ~2.5% fees. Not financial advice.</div>
</body></html>'''
    tmp=os.path.join(D,"dashboard","index.html.tmp"); open(tmp,"w").write(page); os.replace(tmp,os.path.join(D,"dashboard","index.html"))
    print(f"dashboard written: equity ${eq:.2f}")
if __name__=="__main__": build()
