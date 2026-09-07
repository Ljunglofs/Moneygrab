"""
PROP RULES  ·  prop_rules.py
-----------------------------
Propfirmornas regler som hårda spärrar + positionsstorlek för NQ/GC-desken.

Profilerna nedan är hämtade från firmornas publika regelsidor (sept 2026).
VERIFIERA mot ditt eget kontos dashboard — firmor ändrar siffror utan förvarning.
Allt kan överstyras med env-vars (se _env_override).

Env:
    DESK_ACCOUNTS         kommaseparerade profiler som är aktiva, t.ex.
                          "lucid_pro_25k,tradeify_growth_25k". Desken tar den
                          STRÄNGASTE gränsen när flera är aktiva.
    DESK_DAILY_BUDGET_USD egen daglig förlustbudget (default 400, under Tradeifys 600)
    DESK_RISK_PER_TRADE   max risk per trade i USD (default 150)
    DESK_MAX_TRADES_DAY   max antal trades per dag (default 4)
    DESK_MAX_MICROS       tak på micros per position (default från profilen)
"""
import os
from datetime import time as dtime

# Kontraktsspecar (USD per hel punkt, tick-storlek)
CONTRACTS = {
    "NQ": {"mini": ("NQ", 20.0), "micro": ("MNQ", 2.0), "tick": 0.25, "name": "Nasdaq 100"},
    "GC": {"mini": ("GC", 100.0), "micro": ("MGC", 10.0), "tick": 0.10, "name": "Guld"},
}

PROFILES = {
    "lucid_pro_25k": {
        "firm": "Lucid Trading", "plan": "LucidPro", "size": 25_000,
        "profit_target": 1_250,
        "max_loss_eod": 1_000,          # Max Loss Limit, uppdateras vid dagens stängning
        "daily_loss_limit": None,       # 25k har ingen DLL (50k+ har fast DLL)
        "dll_is_soft": None,
        "consistency_pct": 40,          # gäller FUNDED (största dag ≤ 40 % av cykelns vinst)
        "consistency_when": "funded",
        "max_minis": 2, "max_micros": 20,
        "flat_by_et": dtime(16, 45),    # auto-stängning 16:45 ET, failar inte kontot
        "news_allowed": True,
        "source": "support.lucidtrading.com / tradetanto.com (2026)",
    },
    "tradeify_growth_25k": {
        "firm": "Tradeify", "plan": "Growth", "size": 25_000,
        "profit_target": 1_500,
        "max_loss_eod": 1_000,          # EOD trailing max drawdown
        "daily_loss_limit": 600,        # soft breach: dagen stängs, kontot lever
        "dll_is_soft": True,
        "consistency_pct": 35,          # kontrolleras vid payout-begäran
        "consistency_when": "payout",
        "max_minis": None, "max_micros": None,   # EJ VERIFIERAT — sätt DESK_MAX_MICROS
        "flat_by_et": dtime(16, 59),    # positioner likvideras 16:59 ET
        "news_allowed": True,
        "source": "help.tradeify.co / tradetanto.com (2026)",
    },
}


def _env_override(p):
    """Låt env skriva över enskilda fält: PROP_<FÄLT>=värde (t.ex. PROP_MAX_MICROS=10)."""
    p = dict(p)
    for k in ("max_loss_eod", "daily_loss_limit", "max_minis", "max_micros", "consistency_pct"):
        v = os.environ.get("PROP_" + k.upper())
        if v not in (None, ""):
            try:
                p[k] = int(v)
            except ValueError:
                pass
    return p


def active_profiles():
    names = [n.strip() for n in os.environ.get(
        "DESK_ACCOUNTS", "lucid_pro_25k,tradeify_growth_25k").split(",") if n.strip()]
    out = []
    for n in names:
        if n in PROFILES:
            out.append({"key": n, **_env_override(PROFILES[n])})
    return out or [{"key": "tradeify_growth_25k", **_env_override(PROFILES["tradeify_growth_25k"])}]


def effective_limits(profiles=None):
    """Strängaste gränsen över alla aktiva konton + egna (hårdare) budgetar."""
    profiles = profiles or active_profiles()
    dlls = [p["daily_loss_limit"] for p in profiles if p.get("daily_loss_limit")]
    micros = [p["max_micros"] for p in profiles if p.get("max_micros")]
    minis = [p["max_minis"] for p in profiles if p.get("max_minis")]
    env_micros = os.environ.get("DESK_MAX_MICROS")
    if env_micros:
        micros.append(int(env_micros))
    own_budget = int(os.environ.get("DESK_DAILY_BUDGET_USD", "400"))
    firm_dll = min(dlls) if dlls else None
    return {
        "accounts": [f'{p["firm"]} {p["plan"]} {p["size"]//1000}k' for p in profiles],
        "max_loss_eod": min(p["max_loss_eod"] for p in profiles),
        "firm_daily_loss_limit": firm_dll,
        "daily_budget": min(own_budget, firm_dll) if firm_dll else own_budget,
        "risk_per_trade": int(os.environ.get("DESK_RISK_PER_TRADE", "150")),
        "max_trades_day": int(os.environ.get("DESK_MAX_TRADES_DAY", "4")),
        "max_micros": min(micros) if micros else 10,      # försiktig default när firman inte anger
        "max_minis": min(minis) if minis else 1,
        "flat_by_et": min(p["flat_by_et"] for p in profiles),
        "consistency": [f'{p["firm"]}: {p["consistency_pct"]} % ({p["consistency_when"]})'
                        for p in profiles if p.get("consistency_pct")],
        "unverified": [f'{p["firm"]} {p["plan"]}: max kontrakt ej verifierat'
                       for p in profiles if not p.get("max_micros")],
    }


def size_position(inst, stop_points, risk_usd, limits=None):
    """Antal MICRO-kontrakt så att stoppavståndet = risk_usd. Returnerar dict.
    Micros först (finare steg); ≥10 micros redovisas även som minis."""
    limits = limits or effective_limits()
    spec = CONTRACTS[inst]
    micro_sym, micro_val = spec["micro"]
    mini_sym, mini_val = spec["mini"]
    stop_points = abs(float(stop_points))
    if stop_points <= 0:
        return {"micros": 0, "note": "ogiltigt stoppavstånd"}
    per_micro = stop_points * micro_val
    n = int(risk_usd // per_micro)
    capped = False
    if n > limits["max_micros"]:
        n, capped = limits["max_micros"], True
    minis = n // int(mini_val / micro_val)
    return {
        "micros": n, "micro_sym": micro_sym, "usd_per_micro": round(per_micro, 2),
        "risk_usd": round(n * per_micro, 2), "minis_equiv": minis, "mini_sym": mini_sym,
        "capped": capped,
        "text": (f"{n} {micro_sym}" + (f" (≈{minis} {mini_sym})" if minis else "")
                 + f" · risk {round(n * per_micro):.0f} USD" + (" · TAK" if capped else "")
                 if n else f"0 kontrakt — stoppet ({stop_points:g} p = {per_micro:.0f} USD/micro) "
                          f"är för brett för {risk_usd:.0f} USD risk"),
    }


def day_gate(state, now_et, limits=None):
    """Får desken föreslå NYA entries just nu? Returnerar (ok, orsak)."""
    limits = limits or effective_limits()
    pnl = float(state.get("pnl_today", 0.0))
    if pnl <= -limits["daily_budget"]:
        return False, f"daglig budget slut ({pnl:+.0f} USD, gräns -{limits['daily_budget']})"
    if state.get("trades_today", 0) >= limits["max_trades_day"]:
        return False, f"max {limits['max_trades_day']} trades idag nådda"
    cutoff_h, cutoff_m = limits["flat_by_et"].hour, limits["flat_by_et"].minute
    cutoff_min = cutoff_h * 60 + cutoff_m - 20          # inga nya entries sista 20 min
    now_min = now_et.hour * 60 + now_et.minute
    if now_et.weekday() >= 5:
        return False, "helg"
    if now_min >= cutoff_min and now_min < 18 * 60:
        return False, f"för nära flat-tiden {cutoff_h:02d}:{cutoff_m:02d} ET"
    if state.get("halted"):
        return False, "desken är pausad (/desk on för att återuppta)"
    return True, "ok"


def remaining_risk(state, limits=None):
    limits = limits or effective_limits()
    pnl = float(state.get("pnl_today", 0.0))
    left = limits["daily_budget"] + min(pnl, 0)
    per_trade = min(limits["risk_per_trade"], max(0, left / 2) if left < limits["risk_per_trade"] * 2 else limits["risk_per_trade"])
    return {"budget_left": round(left, 2), "risk_next_trade": round(per_trade, 2)}


def rules_text(limits=None):
    limits = limits or effective_limits()
    lines = ["\U0001F6E1 <b>PROP-REGLER (aktiva)</b>", " · ".join(limits["accounts"]),
             f"Max loss EOD: {limits['max_loss_eod']} USD",
             f"Firmans DLL: {limits['firm_daily_loss_limit'] or '—'} USD · egen budget: {limits['daily_budget']} USD/dag",
             f"Risk/trade: {limits['risk_per_trade']} USD · max {limits['max_trades_day']} trades/dag",
             f"Max storlek: {limits['max_micros']} micros",
             f"Flat senast: {limits['flat_by_et'].strftime('%H:%M')} ET (nya entries stoppas 20 min innan)"]
    if limits["consistency"]:
        lines.append("Consistency: " + " | ".join(limits["consistency"]))
    for u in limits["unverified"]:
        lines.append("⚠ " + u)
    return "\n".join(lines)
