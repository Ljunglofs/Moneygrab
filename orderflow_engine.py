"""
GRABIT Orderflow — motor för NQ och Guld
----------------------------------------
Håller en levande bild per instrument och räknar allt från RÅDATA, med källorna
tekniskt åtskilda:

  ORDERBOK  (vilande likviditet)   -> likviditetshyllor
  AFFÄRER   (utförda transaktioner) -> candles, footprint, delta, CVD, VWAP,
                                       volymprofil, aggressionsbubblor, absorption
  GEX       (optionsmarknaden)      -> Call Wall / Put Wall / Gamma Flip

GEX visas aldrig som en orderboksvägg, och inget fylls i när data saknas.

Datakälla i dag (öppen, får visas): Hyperliquid US100 (följer Nasdaq-100-indexet)
och Binance XAUUSDT (följer spotguld). Det är samma prisskala som US100- och
XAUUSD-CFD:er. Terminspriset (NQ/GC) = spot + basis och visas bredvid.
När CME-data finns: lägg till en källa i SOURCES och peka instrumentet dit —
inget annat behöver ändras.
"""
import gzip
import json
import os
import statistics
import threading
import time
from collections import deque

import requests

# ── Instrument och källor ─────────────────────────────────────────────────
INSTR = {
    "NQ": {"name": "Nasdaq 100", "cfd": "US100", "fut": "NQ", "yf": "NQ=F",
           "src": "hl", "coin": "xyz:XYZ100", "src_label": "Hyperliquid US100 (index)",
           "bin": 5.0, "book_sig": 4, "tol": 12.0, "min_bubble_usd": 25_000, "shelf_x": 1.8, "bubble_pct": 0.99},
    "GC": {"name": "Gold", "cfd": "XAUUSD", "fut": "GC", "yf": "GC=F",
           "src": "bn", "sym": "XAUUSDT", "src_label": "Binance XAUUSDT (spot)",
           "bin": 1.0, "book_sig": None, "tol": 2.0, "min_bubble_usd": 150_000, "shelf_x": 3.0, "bubble_pct": 0.995},
}
HL_INFO = "https://api.hyperliquid.xyz/info"
HL_WS = "wss://api.hyperliquid.xyz/ws"
BN = "https://fapi.binance.com/fapi/v1/"
KEEP_MIN = 8 * 60            # minuter som sparas (candles/footprint)
BOOK_EVERY = 3.0             # sekunder mellan boksnapshots
SHELF_WINDOW = 20 * 60       # sekunder som hyllans "beständighet" mäts över


def _now_ms():
    return int(time.time() * 1000)


class Inst:
    """All state för ett instrument. Allt muteras under self.lock."""

    def __init__(self, key, cfg):
        self.key, self.cfg = key, cfg
        self.lock = threading.Lock()
        self.candles = {}                 # minut -> dict(o,h,l,c,v,bv,sv,fp{bin:[bv,sv]})
        self.profile = {}                 # bin -> [bv, sv]   (session)
        self.pv = self.vol = 0.0          # VWAP-ackumulatorer (session)
        self.cvd = 0.0
        self.cvd_by_min = {}
        self.sess_day = None
        self.bubbles = deque(maxlen=400)  # (t, px, side, size, usd)
        self.sizes = deque(maxlen=4000)   # usd per aggressor-händelse (för percentil)
        self.book_hist = deque()          # (t, {bin:(side,usd)})
        self.last_book = None
        self.price = None
        self.last_trade_t = 0
        self.bn_from_id = None
        self.basis = None                 # termin - spot (punkter)
        self.basis_t = 0
        self.status = "starting"
        self.live_from = None          # minut då live-affärerna började (före = historik)
        self.real_mins = set()         # sessionens minuter som räknats från äkta affärer
        self.prof_est = {}             # bin -> [köp, sälj, odelad] uppskattat ur 1-min-candles
        self.pv_est = self.vol_est = 0.0

    # ── Affärer ───────────────────────────────────────────────────────────
    def _session_roll(self, t_ms):
        # Session = CME-dygnet, nollställs 18:00 New York ≈ 22:00 UTC
        day = (t_ms // 1000 - 22 * 3600) // 86400
        if day != self.sess_day:
            self.sess_day = day
            self.profile, self.pv, self.vol, self.cvd = {}, 0.0, 0.0, 0.0
            self.real_mins, self.prof_est, self.pv_est, self.vol_est = set(), {}, 0.0, 0.0

    def add_event(self, t, px, side, size):
        """En aggressor-händelse (en taker-order, ev. över flera priser)."""
        b = self.cfg["bin"]
        usd = px * size
        with self.lock:
            self._session_roll(t)
            m = t // 60000
            if self.live_from is None:
                self.live_from = m
            self.real_mins.add(m)
            c = self.candles.get(m)
            if c is None or c.get("hist"):
                c = self.candles[m] = {"o": px, "h": px, "l": px, "c": px, "v": 0.0, "bv": 0.0, "sv": 0.0, "fp": {}}
                for old in [k for k in self.candles if k < m - KEEP_MIN]:
                    del self.candles[old]
            c["h"], c["l"], c["c"] = max(c["h"], px), min(c["l"], px), px
            c["v"] += size
            k = round(round(px / b) * b, 6)
            row = c["fp"].setdefault(k, [0.0, 0.0])
            prow = self.profile.setdefault(k, [0.0, 0.0])
            if side == "B":
                c["bv"] += size; row[0] += size; prow[0] += size; self.cvd += size
            else:
                c["sv"] += size; row[1] += size; prow[1] += size; self.cvd -= size
            self.cvd_by_min[m] = self.cvd
            self.pv += px * size; self.vol += size
            self.price, self.last_trade_t = px, t
            self.sizes.append(usd)
            if len(self.sizes) > 200:
                thr = max(sorted(self.sizes)[int(len(self.sizes) * self.cfg["bubble_pct"])], self.cfg["min_bubble_usd"])
                if usd >= thr:
                    self.bubbles.append((t, px, side, size, usd))

    # ── Orderbok ──────────────────────────────────────────────────────────
    def add_book(self, bids, asks):
        """bids/asks: [(px, size)], bästa först. Lagras som usd per prisbin."""
        b = self.cfg["bin"] * (2 if self.key == "NQ" else 1)
        snap = {}
        for side, lv in (("bid", bids), ("ask", asks)):
            for px, sz in lv:
                k = round(round(px / b) * b, 6)
                s, u = snap.get(k, (side, 0.0))
                snap[k] = (side, u + px * sz)
        now = time.time()
        with self.lock:
            self.book_hist.append((now, snap))
            while self.book_hist and self.book_hist[0][0] < now - SHELF_WINDOW:
                self.book_hist.popleft()
            self.last_book = snap
            if bids and asks and self.price is None:
                self.price = (bids[0][0] + asks[0][0]) / 2

    def shelves(self, top=3):
        """Hyllor = prisbin med STOR och BESTÄNDIG vilande likviditet.
        Beständig = finns i >= 60 % av snapshots senaste 20 min (filtrerar bort blinkande ordrar)."""
        with self.lock:
            hist = list(self.book_hist)
            last = dict(self.last_book or {})
            px = self.price
        if len(hist) < 5 or not last or px is None:
            return []
        n = len(hist)
        agg = {}
        for _, snap in hist:
            for k, (side, usd) in snap.items():
                a = agg.setdefault(k, [side, 0.0, 0])
                a[1] += usd; a[2] += 1
        med = statistics.median([v[1] / v[2] for v in agg.values()]) or 1
        out = []
        for k, (side, tot, cnt) in agg.items():
            avg = tot / cnt
            persist = cnt / n
            if persist < 0.6 or avg < self.cfg["shelf_x"] * med or k not in last:
                continue
            side_now = "bid" if k < px else "ask"
            out.append({"side": side_now, "px": k, "usd": round(last[k][1]), "avg_usd": round(avg),
                        "x": round(avg / med, 1), "persist": round(persist * 100)})
        bids = sorted([s for s in out if s["side"] == "bid"], key=lambda s: -s["avg_usd"])[:top]
        asks = sorted([s for s in out if s["side"] == "ask"], key=lambda s: -s["avg_usd"])[:top]
        return bids + asks


STATE = {k: Inst(k, c) for k, c in INSTR.items()}


# ── Datainsamling ─────────────────────────────────────────────────────────
def _hl_ws_loop(inst):
    from websockets.sync.client import connect
    coin = inst.cfg["coin"]
    while True:
        try:
            with connect(HL_WS, open_timeout=15, ping_interval=20) as ws:
                ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}))
                sub = {"type": "l2Book", "coin": coin}
                if inst.cfg["book_sig"]:
                    sub["nSigFigs"] = inst.cfg["book_sig"]
                ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
                inst.status = "live"
                last_book = 0.0
                pend = None  # slå ihop fills med samma tid och sida till en aggressor-händelse
                while True:
                    m = json.loads(ws.recv(timeout=60))
                    ch = m.get("channel")
                    if ch == "trades":
                        for tr in m["data"]:
                            t, px, sz, sd = int(tr["time"]), float(tr["px"]), float(tr["sz"]), tr["side"]
                            if pend and pend[0] == t and pend[2] == sd:
                                pend[3] += sz; pend[1] = px
                            else:
                                if pend:
                                    inst.add_event(*pend)
                                pend = [t, px, sd, sz]
                        if pend and _now_ms() - pend[0] > 250:
                            inst.add_event(*pend); pend = None
                    elif ch == "l2Book" and time.time() - last_book >= BOOK_EVERY:
                        lv = m["data"]["levels"]
                        inst.add_book([(float(x["px"]), float(x["sz"])) for x in lv[0]],
                                      [(float(x["px"]), float(x["sz"])) for x in lv[1]])
                        last_book = time.time()
        except Exception as e:
            inst.status = "reconnecting"
            time.sleep(5)


def _bn_loop(inst):
    sym = inst.cfg["sym"]
    last_book = 0.0
    while True:
        try:
            if inst.bn_from_id is None:
                tr = requests.get(BN + "aggTrades", params={"symbol": sym, "limit": 1000}, timeout=10).json()
            else:
                tr = requests.get(BN + "aggTrades", params={"symbol": sym, "fromId": inst.bn_from_id, "limit": 1000}, timeout=10).json()
            # aggTrades med samma tid och sida = samma taker-order -> en händelse
            pend = None
            for a in tr:
                t, px, q, sd = int(a["T"]), float(a["p"]), float(a["q"]), ("S" if a["m"] else "B")
                if pend and pend[0] == t and pend[2] == sd:
                    pend[3] += q; pend[1] = px
                else:
                    if pend:
                        inst.add_event(*pend)
                    pend = [t, px, sd, q]
                inst.bn_from_id = int(a["a"]) + 1
            if pend:
                inst.add_event(*pend)
            if time.time() - last_book >= BOOK_EVERY:
                d = requests.get(BN + "depth", params={"symbol": sym, "limit": 1000}, timeout=10).json()
                inst.add_book([(float(p), float(q)) for p, q in d["bids"]], [(float(p), float(q)) for p, q in d["asks"]])
                last_book = time.time()
            inst.status = "live"
            time.sleep(1.0 if len(tr) < 1000 else 0.2)
        except Exception as e:
            inst.status = "reconnecting"
            time.sleep(5)


def _basis_loop():
    """Basis = termin (Yahoo, ~10 min fördröjd) - spot (vår källa) vid samma tid. Utjämnas."""
    while True:
        for k, inst in STATE.items():
            try:
                import yfinance as yf
                h = yf.Ticker(inst.cfg["yf"]).history(period="1d", interval="1m")
                if h is None or not len(h):
                    continue
                ts = int(h.index[-1].timestamp() // 60)
                fut = float(h["Close"].iloc[-1])
                with inst.lock:
                    c = inst.candles.get(ts)
                    spot = c["c"] if c else None
                if spot:
                    b = fut - spot
                    inst.basis = b if inst.basis is None else inst.basis * 0.7 + b * 0.3
                    inst.basis_t = time.time()
            except Exception:
                pass
        time.sleep(600)


def _backfill(inst):
    """Senaste 8 h candles från börsens egen historik, så att grafen inte börjar tom.
    Guld (Binance-klines) har köp/säljvolym per candle -> delta och CVD bakåt.
    Nasdaq (Hyperliquid-candles) saknar uppdelningen -> bara pris och volym, ingen påhittad delta."""
    end = _now_ms()
    sess0 = ((end // 1000 - 22 * 3600) // 86400 * 86400 + 22 * 3600) * 1000
    start = min(sess0, end - KEEP_MIN * 60000)
    try:
        if inst.cfg["src"] == "bn":
            kl = requests.get(BN + "klines", params={"symbol": inst.cfg["sym"], "interval": "1m", "startTime": start, "limit": 1500}, timeout=15).json()
            rows = [(int(k[0]) // 60000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[9])) for k in kl]
        else:
            cs = requests.post(HL_INFO, json={"type": "candleSnapshot", "req": {"coin": inst.cfg["coin"], "interval": "1m",
                                "startTime": start, "endTime": end}}, timeout=15).json()
            rows = [(int(c["t"]) // 60000, float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c["v"]), None) for c in cs]
    except Exception:
        return
    now_m = end // 60000
    b = inst.cfg["bin"]
    with inst.lock:
        inst._session_roll(end)
        # Uppskattad sessionsprofil: candlens volym fördelas jämnt över dess prisspann.
        # Köp/sälj-delning bara där börsen redovisar den (Binance taker-köp); annars odelad.
        for m, o, h, l, c, v, bv in rows:
            if m * 60000 < sess0 or m >= now_m or m in inst.real_mins or v <= 0:
                continue
            k0, k1 = round(l / b), round(h / b)
            nb = max(1, k1 - k0 + 1)
            for k in range(k0, k1 + 1):
                e = inst.prof_est.setdefault(round(k * b, 6), [0.0, 0.0, 0.0])
                if bv is not None:
                    e[0] += bv / nb; e[1] += (v - bv) / nb
                else:
                    e[2] += v / nb
            tp = (h + l + c) / 3
            inst.pv_est += tp * v; inst.vol_est += v
        cvd = 0.0
        for m, o, h, l, c, v, bv in rows:
            if m >= now_m or m in inst.candles or m < now_m - KEEP_MIN:
                continue
            if bv is not None:
                cvd += bv - (v - bv)
            inst.candles[m] = {"o": o, "h": h, "l": l, "c": c, "v": v, "bv": bv if bv is not None else 0.0,
                               "sv": (v - bv) if bv is not None else 0.0, "fp": {}, "hist": True, "nodelta": bv is None}
            if bv is not None:
                inst.cvd_by_min[m] = cvd
        if rows and inst.price is None:
            inst.price = rows[-1][4]


_DATA_DIR = os.environ.get("DATA_DIR", ".")


def _state_file(key):
    return os.path.join(_DATA_DIR, "of_state_" + key + ".json.gz")


def save_state(inst):
    """Sparar äkta data (profil, candles med footprint, CVD, bubblor) så den överlever omstart."""
    with inst.lock:
        st = {"sess_day": inst.sess_day, "profile": {str(k): v for k, v in inst.profile.items()},
              "pv": inst.pv, "vol": inst.vol, "cvd": inst.cvd, "real_mins": sorted(inst.real_mins),
              "cvd_by_min": {str(k): v for k, v in inst.cvd_by_min.items()},
              "candles": {str(m): dict({x: c[x] for x in ("o", "h", "l", "c", "v", "bv", "sv")},
                                       fp={str(k): r for k, r in c["fp"].items()})
                          for m, c in inst.candles.items() if not c.get("hist")},
              "bubbles": list(inst.bubbles), "sizes": list(inst.sizes)[-1000:], "saved": time.time()}
    try:
        tmp = _state_file(inst.key) + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, _state_file(inst.key))
    except Exception as e:
        print("Orderflow: kunde inte spara " + inst.key + ": " + str(e))


def load_state(inst):
    try:
        with gzip.open(_state_file(inst.key), "rt", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return
    now = _now_ms()
    day = (now // 1000 - 22 * 3600) // 86400
    with inst.lock:
        for m, c in st.get("candles", {}).items():
            m = int(m)
            if m >= now // 60000 - KEEP_MIN:
                c["fp"] = {float(k): r for k, r in c["fp"].items()}
                inst.candles[m] = c
        inst.cvd_by_min.update({int(k): v for k, v in st.get("cvd_by_min", {}).items()})
        inst.bubbles.extend(tuple(b) for b in st.get("bubbles", []))
        inst.sizes.extend(st.get("sizes", []))
        if st.get("sess_day") == day:
            inst.sess_day = day
            inst.profile = {float(k): v for k, v in st.get("profile", {}).items()}
            inst.pv, inst.vol, inst.cvd = st.get("pv", 0.0), st.get("vol", 0.0), st.get("cvd", 0.0)
            inst.real_mins = set(st.get("real_mins", []))


def _save_loop():
    while True:
        time.sleep(60)
        for inst in STATE.values():
            save_state(inst)


_started = False


def start():
    global _started
    if _started:
        return
    _started = True
    for k, inst in STATE.items():
        load_state(inst)
        _backfill(inst)
        fn = _hl_ws_loop if inst.cfg["src"] == "hl" else _bn_loop
        threading.Thread(target=fn, args=(inst,), daemon=True, name=f"of-{k}").start()
    threading.Thread(target=_basis_loop, daemon=True, name="of-basis").start()
    threading.Thread(target=_save_loop, daemon=True, name="of-save").start()


# ── Utdata ────────────────────────────────────────────────────────────────
def _gex_for(inst, gex_data):
    """GEX-nivåer (terminspris) omräknade till spot med basis. Saknas basis: skickas utan spotpris."""
    out = []
    for g in (gex_data or {}).get("levels", []) or []:
        if g.get("inst") != inst.key:
            continue
        basis = inst.basis
        if basis is None and inst.key == "NQ" and g.get("basis") is not None:
            basis = float(g["basis"])
        for key, label, typ in (("call_wall", "Call Wall", "call"), ("put_wall", "Put Wall", "put"),
                                ("zero_gamma", "Gamma Flip", "flip")):
            v = g.get(key)
            if v is None:
                continue
            weak = bool(g.get(key + "_weak"))
            out.append({"label": label + (" (weak)" if weak else ""), "type": typ, "fut": round(float(v), 2),
                        "px": round(float(v) - basis, 2) if basis is not None else None})
        out_meta = {"regime": g.get("regime"), "generated": gex_data.get("generated"),
                    "basis": round(basis, 2) if basis is not None else None}
        return out, out_meta
    return out, {}


def _convergence(inst, price, shelves, gex, prof_lv, bubbles, absorb):
    """Zoner där OBEROENDE källor möts. Varje bevis märks med sin källa."""
    tol = inst.cfg["tol"]
    cands = []
    for s in shelves:
        cands.append((s["px"], "Orderbook", f"{'Bid' if s['side'] == 'bid' else 'Ask'} shelf ~${s['avg_usd'] / 1e6:.1f}M ({s['persist']}% of the time)", s["side"]))
    for g in gex:
        if g["px"] is not None:
            cands.append((g["px"], "GEX (options)", g["label"], None))
    for name, v in prof_lv.items():
        if v is not None:
            cands.append((v, "Volume", name, None))
    for b in bubbles:
        cands.append((b["px"], "Trades", f"Large {'buy' if b['side'] == 'B' else 'sell'} {b['usd'] / 1e3:.0f}", "bid" if b["side"] == "B" else "ask"))
    for a in absorb:
        cands.append((a["px"], "Trades", a["text"], a["side"]))
    zones = []
    used = set()
    for i, (p, src, txt, side) in enumerate(sorted(cands, key=lambda c: c[0])):
        if i in used:
            continue
        grp = [(j, c) for j, c in enumerate(sorted(cands, key=lambda c: c[0])) if abs(c[0] - p) <= tol]
        srcs = {c[1] for _, c in grp}
        if len(srcs) >= 2 and srcs & {"Orderbook", "GEX (options)"} and (srcs & {"GEX (options)", "Trades"} or len(srcs) >= 3):
            for j, _ in grp:
                used.add(j)
            ps = [c[0] for _, c in grp]
            sides = [c[3] for _, c in grp if c[3]]
            side = "bid" if sides.count("bid") > sides.count("ask") else "ask" if sides else ("bid" if p < price else "ask")
            ev, bigs = [], {"B": [0, 0.0], "S": [0, 0.0]}
            for _, c in grp:
                if c[2].startswith("Large "):
                    k = "B" if " buy " in c[2] else "S"
                    bigs[k][0] += 1; bigs[k][1] += float(c[2].split()[2]) * 1e3
                elif {"src": c[1], "text": c[2]} not in ev:
                    ev.append({"src": c[1], "text": c[2]})
            for k, (n, u) in bigs.items():
                if n:
                    ev.append({"src": "Trades", "text": f"{n} large {'buy' if k == 'B' else 'sell'} order{'s' if n > 1 else ''} (${u / 1e6:.2f}M)"})
            zones.append({"lo": min(ps), "hi": max(ps), "side": side, "score": len(srcs), "evidence": ev[:5]})
    zones.sort(key=lambda z: (-z["score"], abs((z["lo"] + z["hi"]) / 2 - price)))
    return zones[:2]


def snapshot(key, tf=1, gex_data=None):
    inst = STATE.get(key)
    if inst is None:
        return None
    tf = max(1, int(tf))
    with inst.lock:
        mins = sorted(inst.candles)
        cand = {m: dict(inst.candles[m]) for m in mins}
        profile = {k: list(v) for k, v in inst.profile.items()}
        pest = {k: list(v) for k, v in inst.prof_est.items()}
        pv_e, vol_e = inst.pv_est, inst.vol_est
        cvdm = dict(inst.cvd_by_min)
        bubbles = list(inst.bubbles)
        price = inst.price
        vwap = (inst.pv + pv_e) / (inst.vol + vol_e) if (inst.vol + vol_e) else None
        cvd = inst.cvd
        status, basis = inst.status, inst.basis
    # candles i vald tidsram
    bars = []
    for m in mins:
        g = m - (m % tf)
        c = cand[m]
        if bars and bars[-1]["t"] == g * 60:
            b = bars[-1]
            b["h"], b["l"], b["c"] = max(b["h"], c["h"]), min(b["l"], c["l"]), c["c"]
            b["v"] += c["v"]; b["bv"] += c["bv"]; b["sv"] += c["sv"]; b["cvd"] = cvdm.get(m, b["cvd"])
        else:
            bars.append({"t": g * 60, "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"],
                         "bv": c["bv"], "sv": c["sv"], "cvd": cvdm.get(m), "nodelta": bool(c.get("nodelta"))})
    # footprint per candle (bara de senaste 40 — det är dem som visas i footprint-läget)
    fpm = {}
    for b in bars[-40:]:
        g0 = b["t"] // 60
        rows = {}
        for m in range(g0, g0 + tf):
            c = cand.get(m)
            if not c:
                continue
            for k, (bv, sv) in c["fp"].items():
                r = rows.setdefault(k, [0.0, 0.0])
                r[0] += bv; r[1] += sv
        if rows:
            fpm[b["t"]] = [[k, round(v[0], 3), round(v[1], 3)] for k, v in sorted(rows.items())]
    for b in bars[-40:]:
        if b["t"] in fpm:
            b["fp"] = fpm[b["t"]]
    # volymprofil (session): POC, VAH/VAL 70 %
    merged = {}
    for k, (b_, s_) in profile.items():
        merged[k] = [b_, s_, b_ + s_, 0.0]
    for k, (eb, es, en) in pest.items():
        r = merged.setdefault(k, [0.0, 0.0, 0.0, 0.0])
        r[0] += eb; r[1] += es; r[2] += eb + es + en; r[3] += eb + es + en
    prof = sorted(merged.items())
    poc = vah = val = None
    if prof:
        tot = sum(r[2] for _, r in prof)
        poc_i = max(range(len(prof)), key=lambda i: prof[i][1][2])
        lo = hi = poc_i; acc = prof[poc_i][1][2]
        while acc < 0.7 * tot and (lo > 0 or hi < len(prof) - 1):
            up = prof[hi + 1][1][2] if hi < len(prof) - 1 else -1
            dn = prof[lo - 1][1][2] if lo > 0 else -1
            if up >= dn:
                hi += 1; acc += up
            else:
                lo -= 1; acc += dn
        poc, vah, val = prof[poc_i][0], prof[hi][0], prof[lo][0]
    shelves = inst.shelves()
    gex, gex_meta = _gex_for(inst, gex_data)
    # bubblor senaste 3 h
    cut = _now_ms() - 3 * 3600 * 1000
    bub = [{"t": t // 1000, "px": px, "side": sd, "size": round(sz, 3), "usd": round(u)} for t, px, sd, sz, u in bubbles if t >= cut]
    # absorption: senaste 15 min, tung aggression mot en hylla som står kvar och priset höll
    absorb = []
    recent = [cand[m] for m in mins[-15:]]
    if recent and price is not None:
        lo15, hi15 = min(c["l"] for c in recent), max(c["h"] for c in recent)
        sv15, bv15 = sum(c["sv"] for c in recent), sum(c["bv"] for c in recent)
        for s in shelves:
            if s["side"] == "bid" and abs(s["px"] - lo15) <= inst.cfg["tol"] and sv15 > 1.5 * bv15 and price > lo15 + inst.cfg["bin"]:
                absorb.append({"px": s["px"], "side": "bid", "text": "Aggressive selling without a new low — bid shelf holding (absorption)"})
            if s["side"] == "ask" and abs(s["px"] - hi15) <= inst.cfg["tol"] and bv15 > 1.5 * sv15 and price < hi15 - inst.cfg["bin"]:
                absorb.append({"px": s["px"], "side": "ask", "text": "Aggressive buying without a new high — ask shelf holding (absorption)"})
    zones = _convergence(inst, price or 0, shelves, gex, {"POC": poc, "VAH": vah, "VAL": val, "VWAP": vwap},
                         bub[-30:], absorb) if price else []
    sess_b = sum(b for b, _ in profile.values()); sess_s = sum(s for _, s in profile.values())
    return {
        "inst": key, "name": inst.cfg["name"], "cfd": inst.cfg["cfd"], "fut": inst.cfg["fut"],
        "source": inst.cfg["src_label"], "status": status, "price": price,
        "basis": round(basis, 2) if basis is not None else gex_meta.get("basis"),
        "tf": tf, "bars": bars[-480:], "vwap": vwap, "cvd": cvd,
        "profile": [{"px": k, "bv": round(r[0], 3), "sv": round(r[1], 3), "v": round(r[2], 3),
                     "est": round(r[3] / r[2], 2) if r[2] else 0} for k, r in prof],
        "profile_est": bool(pest),
        "poc": poc, "vah": vah, "val": val,
        "shelves": shelves, "bubbles": bub[-60:], "absorption": absorb,
        "gex": gex, "gex_meta": gex_meta, "zones": zones,
        "session": {"buy": round(sess_b, 2), "sell": round(sess_s, 2), "delta": round(sess_b - sess_s, 2),
                    "volume": round(sess_b + sess_s, 2)},
        "sources": {"orderbook": inst.cfg["src_label"] + " · order book", "trades": inst.cfg["src_label"] + " · trades",
                    "gex": "GRABIT GEX (CBOE options)"},
        "ts": int(time.time()),
    }
