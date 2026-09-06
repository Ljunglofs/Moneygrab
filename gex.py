# gex.py — Grabit Gamma Exposure
# Kör: python gex.py            (skriver ut NQ + GC)
#      python gex.py nq         (bara Nasdaq)
#      python gex.py gc         (bara guld)
import math, os, sys, datetime as dt
import pandas as pd
import yfinance as yf

R = 0.045          # riskfri ränta
MAX_DAYS = 45      # expiries att räkna med
MARKETS = {
    "nq": dict(etf="QQQ", fut="NQ=F", name="NASDAQ", tick=25),
    "gc": dict(etf="GLD", fut="GC=F", name="GULD", tick=5),
}

def bs_gamma(S, K, T, iv):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (R + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * iv * math.sqrt(T))

def last_price(sym):
    h = yf.Ticker(sym).history(period="5d")
    return float(h["Close"].dropna().iloc[-1])

def snap(x, tick):
    return round(x / tick) * tick

def compute(m):
    t = yf.Ticker(m["etf"])
    S = last_price(m["etf"])
    F = last_price(m["fut"])
    ratio = F / S
    today = dt.date.today()
    rows = []
    for exp in t.options:
        d = dt.date.fromisoformat(exp)
        days = (d - today).days
        if days < 0 or days > MAX_DAYS:
            continue
        T = max(days, 0.5) / 365.0
        ch = t.option_chain(exp)
        for df, sign in ((ch.calls, 1), (ch.puts, -1)):
            for _, o in df.iterrows():
                oi = float(o.get("openInterest") or 0)
                iv = float(o.get("impliedVolatility") or 0)
                if oi <= 0 or iv <= 0:
                    continue
                g = bs_gamma(S, float(o["strike"]), T, iv)
                rows.append((float(o["strike"]), sign * g * oi * 100 * S * S * 0.01))
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["strike", "gex"]).groupby("strike").sum().sort_index()
    total = df["gex"].sum()
    calls = df[df["gex"] > 0]
    puts = df[df["gex"] < 0]
    call_wall = float(calls["gex"].idxmax()) if len(calls) else float("nan")
    put_wall = float(puts["gex"].idxmin()) if len(puts) else float("nan")
    cum = df["gex"].cumsum()
    flips = []
    prev_s, prev_v = None, None
    for s, v in cum.items():
        if prev_v is not None and (prev_v < 0) != (v < 0):
            flips.append(float(s))
        prev_s, prev_v = s, v
    flip = min(flips, key=lambda x: abs(x - S)) if flips else float("nan")
    tick = m["tick"]
    return dict(
        name=m["name"], etf=m["etf"], spot=S, fut=F,
        regime="POSITIV" if total > 0 else "NEGATIV",
        total=total,
        call_wall=call_wall, put_wall=put_wall, flip=flip,
        call_wall_f=snap(call_wall * ratio, tick) if call_wall == call_wall else None,
        put_wall_f=snap(put_wall * ratio, tick) if put_wall == put_wall else None,
        flip_f=snap(flip * ratio, tick) if flip == flip else None,
    )

def fmt(r):
    if r is None:
        return "Ingen data."
    hint = "range · FADE/RTV" if r["regime"] == "POSITIV" else "trend · CONT"
    return (
        f"GEX {r['name']} ({r['etf']} {r['spot']:.2f} → {r['fut']:.0f})\n"
        f"Regim: {r['regime']} gamma ({hint})\n"
        f"Call wall: {r['call_wall_f']}  ({r['call_wall']:.0f} {r['etf']})\n"
        f"Flip:      {r['flip_f']}  ({r['flip']:.0f} {r['etf']})\n"
        f"Put wall:  {r['put_wall_f']}  ({r['put_wall']:.0f} {r['etf']})"
    )

def send_telegram(text):
    tok, chat = os.getenv("TG_TOKEN"), os.getenv("TG_CHAT")
    if not tok or not chat:
        return
    import urllib.request, urllib.parse
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=20)

if __name__ == "__main__":
    keys = [a.lower() for a in sys.argv[1:]] or list(MARKETS)
    out = []
    for k in keys:
        if k in MARKETS:
            out.append(fmt(compute(MARKETS[k])))
    text = "\n\n".join(out)
    print(text)
    send_telegram(text)
