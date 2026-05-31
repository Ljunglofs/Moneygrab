# ============================================================
#  forklaring.py  —  MoneyGrab
#  Bygger en kort textrad som förklarar VARFÖR en aktie
#  hamnade där den hamnade, från siffror motorn redan räknat.
#  Ingen ny data, inga nya beroenden.
# ============================================================
#
#  INKOPPLING i app.py:
#    from forklaring import forklara
#
#  Sedan i render_grid(), lägg "Varför": forklara(a) i dict-raden
#  och en kolumn-config för den (se instruktion i chatten).
# ============================================================


def forklara(a):
    """
    Tar ett scan-/analyze-record och returnerar en kort svensk
    förklaring av läget. Använder fält som redan finns:
    label, rsi, ret_20, pct_from_high, rel_vol, last,
    ema50, ema200 (om de finns).
    """
    bitar = []
    label = a.get("label", "")
    rsi = a.get("rsi", 0)
    ret20 = a.get("ret_20", 0)
    pfh = a.get("pct_from_high", 0)
    relvol = a.get("rel_vol", 1)
    last = a.get("last", 0)
    ema50 = a.get("ema50")
    ema200 = a.get("ema200")

    # 1) trendläge mot glidande medel
    if ema50 is not None and ema200 is not None and last:
        if last > ema50 and last > ema200:
            bitar.append("över EMA50/200")
        elif last > ema50:
            bitar.append("över EMA50, under 200")
        elif last < ema50 and last < ema200:
            bitar.append("under EMA50/200")

    # 2) momentum (20 dagar)
    if ret20 >= 25:
        bitar.append(f"stark uppgång +{ret20:.0f}% 20d")
    elif ret20 >= 8:
        bitar.append(f"+{ret20:.0f}% 20d")
    elif ret20 <= -8:
        bitar.append(f"ned {ret20:.0f}% 20d")

    # 3) närhet till 52v-topp (laddat läge)
    if -8 <= pfh <= 1:
        bitar.append(f"nära 52v-topp ({pfh:.0f}%)")
    elif pfh < -25:
        bitar.append(f"långt under topp ({pfh:.0f}%)")

    # 4) volym
    if relvol >= 2:
        bitar.append(f"hög volym {relvol:.1f}×")
    elif relvol >= 1.3:
        bitar.append(f"volym {relvol:.1f}×")

    # 5) RSI-varning
    if rsi >= 78:
        bitar.append(f"överköpt RSI {rsi:.0f}")
    elif rsi <= 35:
        bitar.append(f"låg RSI {rsi:.0f}")

    # bygg ihop med en kort lägesintro
    intro = {
        "Rocketcase": "Laddar: ",
        "ROCKETCASE": "Laddar: ",
        "BULL": "Stark: ",
        "MOMENTUM": "Fart: ",
        "NEUTRAL/BYGGER": "Bygger: ",
        "VÄNDNING": "Vänder: ",
        "BEAR": "Svag: ",
        "AVSVALNING": "Svalnar: ",
    }.get(label, "")

    if not bitar:
        return intro.rstrip(": ") or "—"
    return intro + ", ".join(bitar)
