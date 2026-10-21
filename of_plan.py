"""Automatisk dagsplan för orderflow-portalen — samma arbetssätt som den manuella planen:
alla nivåer (profil, gårdagen, Asia/London, GEX, hyllor) slås ihop till zoner där de ligger
nära varandra, varje zon får chans att nås i dag och historisk hållbarhet, och de bästa
zonerna på varje sida graderas A+ eller A (LONG under priset, SHORT över). Zoner, inte signaler.
Ändrar ingenting i motorn — bygger bara på det färdiga snapshotet (augment)."""
import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

TOL = {"NQ": 15.0, "GC": 3.5}      # nivåer närmare än så = samma zon (dagsplanen: bara NQ och guld)
PAD = {"NQ": 3.0, "GC": 0.6}       # zonens kant utanför nivåerna
SKIP = {"Session High", "Session Low", "VWAP"}   # rörliga nivåer — inte grund för en plan
_C = {}


def _short(name):
    return (name.replace("Yesterday's ", "gårdagens ").replace("Buyer shelf", "köphylla")
            .replace("Seller shelf", "säljhylla").replace(" (weak)", " (svag)"))


def _flow(snap, side, lo, hi, vol_mean):
    """Orderflödet i zonen: orderboken (hyllor), footprint (volym vid priset), absorption och
    aggressiva affärer. Ger (förklaringar [en, sv], poäng)."""
    out, pts = [], 0.0
    want = "ask" if side == "short" else "bid"
    sh = [x for x in (snap.get("shelves") or []) if lo <= x["px"] <= hi and x["side"] == want]
    if sh:
        b = max(sh, key=lambda x: x.get("avg_usd") or 0)
        pts += min(1.5, sum((x.get("persist") or 0) / 100 * min(1.5, (x.get("x") or 1) / 3) for x in sh))
        usd = (b.get("avg_usd") or 0) / 1e6
        out.append([f"{'Sell' if want == 'ask' else 'Buy'} shelf in the book ~${usd:.1f}M ({b.get('persist', 0)}% of the time)",
                    f"{'säljhylla' if want == 'ask' else 'köphylla'} i orderboken ~${usd:.1f}M"])
    rows = [r for r in (snap.get("profile") or []) if lo <= r["px"] <= hi and r.get("v")]
    if rows and vol_mean > 0:
        ratio = (sum(r["v"] for r in rows) / len(rows)) / vol_mean
        if ratio >= 1.3:
            pts += min(1.0, (ratio - 1) / 2)
            out.append([f"Footprint: heavy volume traded here ({ratio:.1f}× average)", f"footprint: tung volym ({ratio:.1f}× snittet)"])
    ab = [a for a in (snap.get("absorption") or []) if lo - 2 <= a["px"] <= hi + 2 and a.get("side") == want]
    if ab:
        pts += 1.0
        out.append(["Absorption: " + ab[0]["text"].split(" — ")[0].lower() + " — the level is being defended", "absorption: nivån försvaras"])
    bb = [b for b in (snap.get("bubbles") or []) if lo <= b["px"] <= hi]
    if bb:
        pts += min(0.5, 0.15 * len(bb))
        usd = sum(b.get("usd") or 0 for b in bb) / 1e6
        out.append([f"{len(bb)} large aggressive trade{'s' if len(bb) > 1 else ''} here (${usd:.2f}M)", ("1 stor aggressiv affär" if len(bb) == 1 else f"{len(bb)} stora aggressiva affärer")])
    if any(z["lo"] <= hi and z["hi"] >= lo for z in (snap.get("zones") or [])):
        pts += 0.5
        out.append(["Overlaps a live orderflow confluence zone", "överlappar en live-konfluenszon"])
    return out, round(pts, 2)


def build(snap):
    """Plan ur ett färdigt snapshot (med levels/p/hold). Priser i orderflödets pris."""
    inst, price = snap.get("inst"), snap.get("price")
    lv = [l for l in (snap.get("levels") or []) if l.get("px") is not None and l["name"] not in SKIP]
    if not lv or price is None or inst not in TOL:
        return None
    base = (snap.get("hold_base") or [0.5])[0]
    tol, pad = TOL[inst], PAD[inst]
    lv.sort(key=lambda l: l["px"])
    clusters, cur = [], [lv[0]]
    for l in lv[1:]:
        if l["px"] - cur[-1]["px"] <= tol and l["px"] - cur[0]["px"] <= 1.5 * tol:
            cur.append(l)
        else:
            clusters.append(cur); cur = [l]
    clusters.append(cur)
    prof = [r for r in (snap.get("profile") or []) if r.get("v")]
    vol_mean = (sum(r["v"] for r in prof) / len(prof)) if prof else 0.0
    zones = []
    for c in clusters:
        names = list(dict.fromkeys(l["name"] for l in c))
        lo, hi = c[0]["px"] - pad, c[-1]["px"] + pad
        side = "short" if lo > price else "long" if hi < price else None
        if side is None:
            continue                                  # priset står i zonen — ingen ny plan där
        kant = lo if side == "short" else hi
        if abs(kant - price) < 0.3 * tol:
            continue                                  # för nära priset — inte en plan, det är där vi står
        near = min(c, key=lambda l: abs(l["px"] - kant))
        reach = dict(near.get("p") or {})
        hl = [l["hold"] for l in c if l.get("hold")]
        best = max(hl, key=lambda h: h[0]) if hl else None
        edge = (best[0] - base) if best else 0.0          # hållbarhet mot slumpnivåer
        gex = [l["name"] for l in c if l.get("group") == "gex"]
        conf = min(len(names), 5)
        fl, fpts = _flow(snap, side, lo - pad, hi + pad, vol_mean)
        pc = reach.get("close") or 0.0
        # Kvalitet: oberoende källor + historisk hållbarhet + optioner + orderbok,
        # viktad med modellens chans att priset alls når zonen i dag.
        q = conf + max(-1.0, min(2.0, edge / 0.10)) + (1.0 if gex else 0.0) + fpts
        total = q * (0.4 + 0.6 * min(1.0, pc / 0.5))
        grade = "A+" if total >= 4.5 and conf >= 3 and edge > -0.05 else "A" if total >= 2.8 else None
        if not grade:
            continue
        if sh_in := [f for f in fl if "shelf" in f[0]]:
            names = [n for n in names if "shelf" not in n] or names   # hyllan står redan under orderboken
        zones.append({
            "side": side, "lo": round(lo, 2), "hi": round(hi, 2), "score": round(total, 2), "grade": grade,
            "name": grade + (" SHORT" if side == "short" else " LONG"), "sources": names,
            "reach": {k: round(v, 3) for k, v in reach.items()},
            "hold": [round(best[0], 3), best[1]] if best else None, "edge": round(edge, 3),
            "gex": gex, "flow": fl,
        })
    out = []
    for side in ("short", "long"):
        zs = [z for z in zones if z["side"] == side]
        zs.sort(key=lambda z: (z["grade"] != "A+", -z["score"]))
        out += zs[:2]                                     # högst två zoner per sida — inget plotter
    gm = snap.get("gex_meta") or {}
    return {"inst": inst, "generated": int(time.time()), "price": price, "regime": gm.get("regime"),
            "zones": out}


def get(snap, max_age=600):
    """Senaste planen för instrumentet; byggs om var 10:e minut."""
    inst = snap.get("inst")
    c = _C.get(inst)
    if c and time.time() - c["generated"] < max_age:
        return c
    p = build(snap)
    if p:
        _C[inst] = p
    return p or c


def telegram_text(plan, basis=0.0, fut=True, dec=2):
    """Planen som Telegram-meddelande (svenska, futurespriser för NQ/GC)."""
    if not plan or not plan.get("zones"):
        return None
    o = basis if fut and basis else 0.0
    f = lambda v: f"{v + o:,.{dec}f}".replace(",", " ")
    pc = lambda p: "<1%" if p is not None and p < .01 else ("%d%%" % round(p * 100) if p is not None else "–")
    name = {"NQ": "Nasdaq (NQ)", "GC": "Guld (GC)", "BTC": "Bitcoin"}.get(plan["inst"], plan["inst"])
    t = datetime.fromtimestamp(plan["generated"], ZoneInfo("Europe/Stockholm")).strftime("%H:%M")
    reg = {"negativ": "negativ gamma — rörelser kan förstärkas", "positiv": "positiv gamma — studsar gärna mellan väggarna"}.get(plan.get("regime"), "")
    lines = [f"📋 <b>GRABIT DAGSPLAN · {name}</b> · {t}", f"Pris {f(plan['price'])}" + (f" · {reg}" if reg else ""), ""]
    for z in sorted(plan["zones"], key=lambda z: -z["hi"]):
        icon = "🔴" if z["side"] == "short" else "🟢"
        hold = f" · håller {round(z['hold'][0] * 100)}% historiskt" if z.get("hold") else ""
        lines.append(f"{icon} <b>{z['name']}-zon {f(z['lo'])}–{f(z['hi'])}</b>")
        lines.append(f"   chans att nå idag {pc(z['reach'].get('close'))}{hold}")
        lines.append("   " + " + ".join(_short(n) for n in z["sources"][:5]))
        if z.get("flow"):
            lines.append("   " + " · ".join(f[1] for f in z["flow"][:3]))
    lines += ["", "Statistik som underlag — inte råd."]
    return "\n".join(lines)


# ── Utskick till Telegram: vardagar 08:00 och 15:30 svensk tid ─────────────
SLOTS = ("08:00", "15:30")
_SENT = os.path.join(os.environ.get("DATA_DIR", "."), "of_plan_sent.json")


def _sent_load():
    try:
        with open(_SENT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def send_now(make_snap, insts=("NQ", "GC")):
    """Bygger planen ur färska snapshots och skickar den. make_snap(inst) -> fullt snapshot."""
    import nasdaq_robber as _NR
    members = os.environ.get("TELEGRAM_MEMBERS_CHAT_ID", "").strip()
    to_members = os.environ.get("OF_PLAN_MEMBERS", "").strip() == "1" and members
    ok = False
    for inst in insts:
        try:
            snap = make_snap(inst)
            plan = build(snap) if snap else None
            txt = telegram_text(plan, snap.get("basis") or 0.0, dec=2 if inst == "NQ" else 1) if plan else None
            if not txt:
                print(f"[dagsplan] {inst}: ingen plan just nu")
                continue
            _C[inst] = plan
            ok = _NR.send_telegram(txt) or ok                     # egen chatt (CHAT_ID)
            if to_members:
                _NR.send_telegram(txt, chat_id=members)           # medlemskanalen, bara om OF_PLAN_MEMBERS=1
        except Exception as e:
            print(f"[dagsplan] {inst} misslyckades: {e}")
    return ok


def _loop(make_snap):
    tz = ZoneInfo("Europe/Stockholm")
    while True:
        try:
            now = datetime.now(tz)
            hm = now.strftime("%H:%M")
            if now.weekday() < 5 and hm in SLOTS:
                key = now.strftime("%Y-%m-%d ") + hm
                sent = _sent_load()
                if key not in sent:
                    sent[key] = int(time.time())
                    try:
                        with open(_SENT, "w", encoding="utf-8") as f:
                            json.dump(dict(list(sent.items())[-20:]), f)
                    except Exception:
                        pass
                    send_now(make_snap)
        except Exception as e:
            print(f"[dagsplan] schemaläggaren: {e}")
        time.sleep(20)


def start(make_snap):
    if os.environ.get("OF_PLAN_TELEGRAM", "1").strip() == "0":
        return
    threading.Thread(target=_loop, args=(make_snap,), daemon=True, name="of_plan").start()
