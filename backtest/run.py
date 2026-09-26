"""Backtest av GRABIT-scannerns poängmotor.

Kör exakt samma analyze() som appen (sok_module) bakåt i tiden över hela
universumet och mäter vad som hände efteråt. Frågan är enkel: när motorn
sa "BULL, score 8", gick aktien bättre än marknaden de följande dagarna?

Metod
  * Kursdata: 3 år dagsdata från Yahoo för alla aktier + S&P 500 (^GSPC).
  * Utvärderingsdagar: var 5:e handelsdag det senaste året (där 20 dagar
    framåt finns). Motorn får bara se data TILL OCH MED den dagen.
  * Köp på nästa dags öppning (man kan inte köpa på stängningen man mätte på),
    sälj på stängningen efter 5, 10 och 20 handelsdagar.
  * Alfa = aktiens avkastning minus S&P 500 över samma fönster.
  * Affär: motorns egen entry/stopp/mål (breakout_engine) — vad kom först
    inom 20 dagar, stoppen eller mål 1? Mäts i R (risk-multiplar).

Resultat: backtest/results/summary.json och backtest/results/REPORT.md.

Kör:  python backtest/run.py            (hela universumet)
      python backtest/run.py --quick    (40 aktier, för att testa)
"""
import os
import sys
import json
import math
import time
import argparse
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
sys.path.insert(0, HERE)

from universe import UNIVERSE  # noqa: E402

OUT = os.path.join(HERE, "results")
HORIZONS = (5, 10, 20)
STEP = 5            # utvärdera var 5:e handelsdag
LOOKBACK = 252      # så mycket historik motorn får se (samma som appen: 1 år)
DAYS_BACK = 260     # hur långt bak utvärderingen går


def market_of(t):
    if "." not in t:
        return "US"
    return t.rsplit(".", 1)[-1]


def download(tickers, period="3y"):
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), 80):
        part = tickers[i:i + 80]
        for attempt in range(3):
            try:
                data = yf.download(part, period=period, interval="1d", auto_adjust=False,
                                   group_by="ticker", threads=True, progress=False)
                break
            except Exception as e:
                print("nedladdning misslyckades (%s), försöker igen" % e)
                time.sleep(5)
        else:
            continue
        for t in part:
            try:
                df = data[t].dropna(how="all") if len(part) > 1 else data.dropna(how="all")
            except Exception:
                continue
            if df is not None and len(df) >= LOOKBACK + 60:
                df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
                out[t] = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        print("hämtat %d/%d" % (min(i + 80, len(tickers)), len(tickers)), flush=True)
    return out


def rs_vs_bench(close, bench):
    """Samma som api._rs_vs_bench: RS-linjen = kurs / index."""
    j = pd.concat([close, bench], axis=1, join="inner").dropna()
    if len(j) < 64:
        return None, None, None
    line = j.iloc[:, 0] / j.iloc[:, 1]
    c, b = j.iloc[:, 0], j.iloc[:, 1]
    rs20 = float((c.iloc[-1] / c.iloc[-21] - b.iloc[-1] / b.iloc[-21]) * 100)
    rs_up = bool(line.iloc[-1] > line.tail(10).mean() and line.iloc[-1] > line.iloc[-11])
    rs_hi = bool(line.iloc[-1] >= line.tail(63).max() * 0.995)
    return rs20, rs_up, rs_hi


def eval_ticker(args):
    """Alla utvärderingsdagar för en aktie. Körs i en egen process."""
    t, df, bench = args
    from sok_module import analyze
    from breakout_engine import evaluate
    rows = []
    n = len(df)
    start = max(LOOKBACK, n - DAYS_BACK - max(HORIZONS) - 1)
    for i in range(start, n - max(HORIZONS) - 1, STEP):
        hist = df.iloc[max(0, i - LOOKBACK + 1): i + 1]
        if len(hist) < 200:
            continue
        try:
            a = analyze(hist)
        except Exception:
            continue
        try:
            eng = evaluate(hist, bench.loc[:hist.index[-1]])
        except Exception:
            eng = None
        entry = float(df["Open"].iloc[i + 1])
        if not entry or math.isnan(entry):
            continue
        d = hist.index[-1]
        rec = {
            "date": d.strftime("%Y-%m-%d"), "ticker": t, "market": market_of(t),
            "label": a["label"], "score10": int(a["score10"]), "total": float(a["total"]),
            "strength": float(a["strength"]), "momentum": float(a["momentum"]), "setup": float(a["setup"]),
            "rsi": float(a["rsi"]) if a["rsi"] == a["rsi"] else None,
            "rel_vol": float(a["rel_vol"]), "pct_from_high": float(a["pct_from_high"]),
            "tight": float(a["tight"]), "cooling": bool(a["cooling"]), "ret_20": float(a["ret_20"]),
            "rng_pos": float(a["rng_pos"]), "rs_raw": a.get("rs_raw"),
            "dollar_vol": float(a.get("dollar_vol") or 0),
            "above50": bool(a["last"] > a["ema50"]), "above200": bool(a["last"] > a["ema200"]),
            "bos": a.get("bos"), "setup_grade": a.get("setup_grade"),
        }
        rs20, rs_up, rs_hi = rs_vs_bench(hist["Close"], bench.loc[:d])
        rec.update({"rs_20": rs20, "rs_up": rs_up, "rs_line_hi": rs_hi})
        if eng:
            rec.update({"breakout": eng["breakout_score"], "fake": eng["fake_risk"],
                        "stop": eng["entry"]["stop"], "t1": eng["entry"]["t1"]})
        # Framåt: köp nästa öppning, sälj stängning efter h dagar.
        for h in HORIZONS:
            ex = float(df["Close"].iloc[i + h])
            rec["r%d" % h] = (ex / entry - 1) * 100
            b0 = bench.loc[:df.index[i + 1]]
            b1 = bench.loc[:df.index[i + h]]
            if len(b0) and len(b1):
                rec["m%d" % h] = (float(b1.iloc[-1]) / float(b0.iloc[-1]) - 1) * 100
        # Affär med motorns stopp och mål 1, max 20 dagar.
        if eng and eng["entry"]["stop"] and eng["entry"]["stop"] < entry:
            stop, t1 = float(eng["entry"]["stop"]), float(eng["entry"]["t1"])
            risk = entry - stop
            outcome = None
            fut = df.iloc[i + 1: i + 1 + 20]
            for _, bar in fut.iterrows():
                if bar["Low"] <= stop:
                    outcome = -1.0
                    break
                if bar["High"] >= t1:
                    outcome = (t1 - entry) / risk
                    break
            if outcome is None:
                outcome = (float(fut["Close"].iloc[-1]) - entry) / risk
            rec["trade_r"] = round(outcome, 3)
        rows.append(rec)
    return rows


def add_rs_rating(df):
    """RS-rating 1-99 per datum, som i appen (percentil av rs_raw)."""
    df["rs_rating"] = (df.groupby("date")["rs_raw"].rank(pct=True) * 98 + 1).round()
    return df


def regime(bench):
    """RISK_ON om S&P är över sitt 50-dagarssnitt och högre än för 20 dagar sen."""
    ma = bench.rolling(50).mean()
    on = (bench > ma) & (bench > bench.shift(20))
    return {d.strftime("%Y-%m-%d"): ("RISK_ON" if v else "RISK_OFF") for d, v in on.items()}


def stats(g, h=10):
    g = g.dropna(subset=["r%d" % h])
    if not len(g):
        return None
    r = g["r%d" % h]
    alpha = (g["r%d" % h] - g["m%d" % h]).dropna()
    out = {"n": int(len(g)),
           "win": round(float((r > 0).mean() * 100), 1),
           "avg": round(float(r.mean()), 2),
           "median": round(float(r.median()), 2),
           "alpha": round(float(alpha.mean()), 2) if len(alpha) else None,
           "beat_mkt": round(float((alpha > 0).mean() * 100), 1) if len(alpha) else None}
    if "trade_r" in g and g["trade_r"].notna().any():
        tr = g["trade_r"].dropna()
        out["trade_avg_r"] = round(float(tr.mean()), 2)
        out["trade_win"] = round(float((tr > 0).mean() * 100), 1)
    return out


def table(df, key, h=10, order=None):
    res = {}
    keys = order or sorted(df[key].dropna().unique(), key=lambda x: str(x))
    for k in keys:
        s = stats(df[df[key] == k], h)
        if s and s["n"] >= 30:
            res[str(k)] = s
    return res


def rule_sets(df):
    """Varianter av pick-reglerna. 'nu' = reglerna appen använde före steg 1."""
    bull = df["label"].isin(["BULL", "MOMENTUM", "Rocketcase", "VÄNDNING", "NEUTRAL/BYGGER"])
    base = bull & ~df["cooling"] & df["above50"] & (df["rel_vol"] >= 1.1) & (df["score10"] >= 6)
    off = df["regime"] == "RISK_OFF"
    tier_a_old = base & (~off | (df["above200"] & (df["score10"] >= 7)))
    rs70 = df["rs_rating"] >= 70
    liq = df["dollar_vol"] >= 5e6
    return {
        "Alla (universumets snitt)": pd.Series(True, index=df.index),
        "A-läge, gamla reglerna": tier_a_old,
        "A-läge + RS>=70 + likviditet (steg 1)": tier_a_old & rs70 & liq,
        "RS>=80": df["rs_rating"] >= 80,
        "RS>=90": df["rs_rating"] >= 90,
        "RS>=80 + över EMA50 + inte avsvalnande": (df["rs_rating"] >= 80) & df["above50"] & ~df["cooling"],
        "RS-linje på 3-mån-högsta": df["rs_line_hi"] == True,  # noqa: E712
        "RS-linje högsta + inom 10 % från topp": (df["rs_line_hi"] == True) & (df["pct_from_high"] >= -10),  # noqa: E712
        "Rocketcase + RS>=70": (df["label"] == "Rocketcase") & rs70,
        "Score >= 8": df["score10"] >= 8,
        "Score >= 8 + RS>=70": (df["score10"] >= 8) & rs70,
        "Avsvalnande (cooling)": df["cooling"] == True,  # noqa: E712
        "Under EMA50": ~df["above50"],
        "Nära toppen (<=3 %) + RSI > 70": (df["pct_from_high"] >= -3) & (df["rsi"] > 70),
        "Pullback i trend: över EMA50, RSI 40-55, RS>=70": df["above50"] & df["rsi"].between(40, 55) & rs70,
    }


def top_pick(df, rank_fn, mask):
    """En pick per datum (som Top Opportunity): högst rank bland mask."""
    out = []
    # Per vecka: utvärderingsdagarna skiljer sig någon dag mellan aktier.
    wk = pd.to_datetime(df["date"]).dt.to_period("W").astype(str)
    for d, g in df[mask].groupby(wk[mask]):
        g = g[g["market"] == "US"]
        if not len(g):
            continue
        out.append(g.loc[rank_fn(g).idxmax()])
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    tickers = sorted({t for v in UNIVERSE.values() for t in v})
    if args.quick:
        tickers = tickers[:40]
    t0 = time.time()
    data = download(tickers + ["^GSPC"])
    bench = data.pop("^GSPC")["Close"]
    print("data för %d aktier (%.0f s)" % (len(data), time.time() - t0), flush=True)

    rows = []
    jobs = [(t, df, bench) for t, df in data.items()]
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 2) as ex:
        for k, r in enumerate(ex.map(eval_ticker, jobs, chunksize=4)):
            rows.extend(r)
            if k % 50 == 0:
                print("utvärderat %d/%d aktier, %d rader (%.0f s)" % (k, len(jobs), len(rows), time.time() - t0), flush=True)
    df = pd.DataFrame(rows)
    df = add_rs_rating(df)
    # RS-rating per vecka (samma skäl som i top_pick).
    df["rs_rating"] = (df.groupby(pd.to_datetime(df["date"]).dt.to_period("W").astype(str))["rs_raw"]
                       .rank(pct=True) * 98 + 1).round()
    reg = regime(bench)
    df["regime"] = df["date"].map(reg)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "rows.csv.gz"), index=False, compression="gzip")

    df["score_b"] = df["score10"].clip(1, 10)
    df["rs_dec"] = ((df["rs_rating"] - 1) // 10 * 10).clip(0, 90).astype("Int64")
    df["pfh_b"] = pd.cut(df["pct_from_high"], [-100, -30, -15, -8, -3, 1], labels=["<-30", "-30..-15", "-15..-8", "-8..-3", "-3..0"])
    df["bo_b"] = pd.cut(df["breakout"], [-1, 30, 45, 60, 75, 101], labels=["0-30", "30-45", "45-60", "60-75", "75+"]) if "breakout" in df else None

    summary = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
               "tickers": int(df["ticker"].nunique()), "rows": int(len(df)),
               "period": [df["date"].min(), df["date"].max()], "horizons": {}}
    for h in HORIZONS:
        H = {"label": table(df, "label", h), "score10": table(df, "score_b", h, order=list(range(1, 11))),
             "rs_decile": table(df, "rs_dec", h, order=list(range(0, 100, 10))),
             "pct_from_high": table(df, "pfh_b", h, order=["<-30", "-30..-15", "-15..-8", "-8..-3", "-3..0"]),
             "rules": {k: stats(df[m.fillna(False)], h) for k, m in rule_sets(df).items()}}
        if "bo_b" in df:
            H["breakout_score"] = table(df, "bo_b", h, order=["0-30", "30-45", "45-60", "60-75", "75+"])
        summary["horizons"][str(h)] = H

    # Dagens pick som appen väljer den: gamla rankningen mot en med RS.
    rules = rule_sets(df)
    old_rank = lambda g: g["setup"] * 0.6 + g["score10"] * 4.0  # noqa: E731
    new_rank = lambda g: g["setup"] * 0.6 + g["score10"] * 4.0 + g["rs_rating"].fillna(50) * 0.25  # noqa: E731
    picks = {}
    for name, fn, mask in [("Top pick, gamla reglerna", old_rank, rules["A-läge, gamla reglerna"]),
                           ("Top pick, steg 1-reglerna", new_rank, rules["A-läge + RS>=70 + likviditet (steg 1)"]),
                           ("Top pick, RS minus setup (appen nu)", lambda g: g["rs_rating"] - g["setup"],
                            rules["A-läge + RS>=70 + likviditet (steg 1)"]),
                           ("Top pick, högst RS bland RS>=80+EMA50", lambda g: g["rs_rating"],
                            rules["RS>=80 + över EMA50 + inte avsvalnande"])]:
        tp = top_pick(df, fn, mask.fillna(False))
        picks[name] = {str(h): stats(tp, h) for h in HORIZONS} if len(tp) else None
    summary["top_pick"] = picks

    with open(os.path.join(OUT, "summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)
    write_report(summary)
    print("klart på %.0f s" % (time.time() - t0))


def write_report(S):
    L = ["# Backtest av GRABIT-scannern", "",
         "Genererad %s · %d aktier · %d mätpunkter · %s – %s" % (S["generated"], S["tickers"], S["rows"], *S["period"]), "",
         "Köp nästa dags öppning, sälj stängning efter N handelsdagar. **Alfa** = avkastning minus S&P 500 samma period. "
         "**Affär R** = motorns egen stopp/mål 1 inom 20 dagar, i risk-multiplar (−1 = stoppad).", ""]

    def tab(title, d):
        if not d:
            return
        L.append("### " + title)
        L.append("")
        L.append("| | n | träff % | snitt % | median % | alfa % | slog S&P % | affär R | affär träff % |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for k, v in d.items():
            if not v:
                continue
            L.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s |" % (
                k, v["n"], v["win"], v["avg"], v["median"], v.get("alpha"), v.get("beat_mkt"),
                v.get("trade_avg_r", ""), v.get("trade_win", "")))
        L.append("")
    for h in ("10", "5", "20"):
        H = S["horizons"][h]
        L.append("## %s handelsdagar" % h)
        L.append("")
        tab("Regelvarianter", H["rules"])
        tab("Per etikett", H["label"])
        tab("Per score (1–10)", H["score10"])
        tab("Per RS-rating (decil)", H["rs_decile"])
        tab("Avstånd till 52v-högsta", H["pct_from_high"])
        tab("Breakout-score (aktiekortets motor)", H.get("breakout_score"))
    L.append("## Dagens pick (en per utvärderingsdag, USA)")
    L.append("")
    for k, v in (S.get("top_pick") or {}).items():
        if v:
            tab(k, v)
    with open(os.path.join(OUT, "REPORT.md"), "w") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
