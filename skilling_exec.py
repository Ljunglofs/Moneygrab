#!/usr/bin/env python3
"""
GRABIT · Skilling / cTrader execution adapter
=============================================

Fristående process som läser NASDAQ ROBBER:s signalkö och lägger ordrar på
ett Skilling cTrader-konto via cTrader Open API (Spotware). Scannern skriver
BARA en kö-rad (skilling_queue.jsonl) — den här processen autentiserar mot
mäklaren och exekverar. En hängande anslutning kan därför aldrig frysa
scannern.

SÄKERHET (viktigt — läs innan skarp handel)
--------------------------------------------
· CT_MODE=demo som standard. Sätt live FÖRST när demo bevisat sig.
· CT_DRY_RUN=1 som standard: loggar TÄNKTA ordrar men skickar INGET. Kör så
  först, jämför loggad volym mot en manuell demo-order av önskad storlek,
  och slå av dry-run först när siffrorna stämmer.
· Volym: sätt SKILLING_VOLUME till det RÅA cTrader-volymvärdet för den
  storlek du vill handla (läs av det när du lägger en testorder i cTrader).
  Det sidsteppar all enhets-matematik och är det säkra sättet att gå live.
· Kill-switch: överstiger dagens realiserade förlust CT_MAX_DD_CCY (konto-
  valuta) stängs handeln av tills processen startas om.
· CT_MAX_OPEN begränsar antal samtidiga positioner (default 1).

Kräver:  pip install ctrader-open-api  (drar in twisted + protobuf)

Setup i korthet
---------------
1. Öppna ett cTrader-konto på Skilling (välj Demo först).
2. connect.spotware.com -> skapa en app -> ClientId + ClientSecret.
3. Kör OAuth-flödet -> accessToken + ctidTraderAccountId (konto-id).
4. Sätt env (se nedan) och kör:  python skilling_exec.py
"""
import os
import json
import time
from datetime import datetime, timezone


# --------------------------------------------------------------------------
# Konfiguration (env)
# --------------------------------------------------------------------------
DATA_DIR   = os.environ.get("DATA_DIR", ".")
QUEUE      = os.path.join(DATA_DIR, "skilling_queue.jsonl")
FILLS      = os.path.join(DATA_DIR, "skilling_fills.jsonl")
STATE      = os.path.join(DATA_DIR, "skilling_state.json")

MODE       = os.environ.get("CT_MODE", "demo").lower()          # demo | live
DRY_RUN    = os.environ.get("CT_DRY_RUN", "1") == "1"           # 1 = skicka inget
POLL_SEC   = float(os.environ.get("CT_POLL_SEC", "3"))

CLIENT_ID     = os.environ.get("CT_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CT_CLIENT_SECRET", "")
ACCESS_TOKEN  = os.environ.get("CT_ACCESS_TOKEN", "")
ACCOUNT_ID    = int(os.environ.get("CT_ACCOUNT_ID", "0") or 0)  # ctidTraderAccountId

SYMBOL_NAME   = os.environ.get("CT_SYMBOL_NAME", "US 100")      # så det heter i cTrader
SYMBOL_ID_ENV = int(os.environ.get("CT_SYMBOL_ID", "0") or 0)   # valfri override

# Volym: RÅTT cTrader-volymvärde (säkraste vägen). 0 = försök auto-siza.
FIXED_VOLUME  = int(os.environ.get("SKILLING_VOLUME", "0") or 0)
RISK_CCY      = float(os.environ.get("SKILLING_RISK_CCY", "0") or 0)  # risk/trade i kontovaluta (auto-size)

MAX_OPEN      = int(os.environ.get("CT_MAX_OPEN", "1"))
MAX_DD_CCY    = float(os.environ.get("CT_MAX_DD_CCY", "0") or 0)      # 0 = ingen kill-switch
ONLY_SOURCES  = [s.strip() for s in os.environ.get("CT_ONLY_SOURCES", "").split(",") if s.strip()]
MAX_SIGNAL_AGE = int(os.environ.get("CT_MAX_SIGNAL_AGE_SEC", "180"))  # hoppa signaler äldre än detta


def _log(*a):
    print("[skilling-exec]", *a, flush=True)


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"processed": [], "day": "", "start_balance": None, "halted": False}


def _save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def _append_fill(rec):
    with open(FILLS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _read_new_signals(state):
    """Returnera okvitterade kö-rader (dedup på id)."""
    if not os.path.exists(QUEUE):
        return []
    seen = set(state.get("processed", []))
    out = []
    with open(QUEUE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rid = rec.get("id")
            if not rid or rid in seen:
                continue
            out.append(rec)
    return out


def _too_old(rec):
    try:
        ts = datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() > MAX_SIGNAL_AGE
    except Exception:
        return False


# --------------------------------------------------------------------------
# cTrader Open API (Twisted-reaktor). Lazy-importeras så att en miljö utan
# SDK:t inte kraschar (t.ex. om man bara vill dry-run:a kön).
# --------------------------------------------------------------------------
class CTraderExecutor:
    def __init__(self):
        self.client = None
        self.reactor = None
        self.symbol_id = SYMBOL_ID_ENV or None
        self.symbol = None            # detaljer (pipPosition, minVolume, stepVolume...)
        self.authed = False
        self.state = _load_state()
        self.open_count = 0

    # ---- publikt ----
    def start(self):
        from ctrader_open_api import Client, TcpProtocol, EndPoints
        from twisted.internet import reactor, task
        self.reactor = reactor
        host = EndPoints.PROTOBUF_LIVE_HOST if MODE == "live" else EndPoints.PROTOBUF_DEMO_HOST
        _log(f"ansluter mot cTrader {MODE.upper()} ({host}:{EndPoints.PROTOBUF_PORT}) "
             f"· DRY_RUN={'PÅ' if DRY_RUN else 'AV'}")
        self.client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self.client.setConnectedCallback(self._on_connected)
        self.client.setDisconnectedCallback(self._on_disconnected)
        self.client.setMessageReceivedCallback(self._on_message)
        self.client.startService()
        task.LoopingCall(self._poll).start(POLL_SEC, now=False)
        reactor.run()

    # ---- anslutning / auth ----
    def _on_connected(self, client):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAApplicationAuthReq
        req = ProtoOAApplicationAuthReq()
        req.clientId = CLIENT_ID
        req.clientSecret = CLIENT_SECRET
        d = client.send(req)
        d.addErrback(lambda f: _log("app-auth-fel:", f))

    def _on_disconnected(self, client, reason):
        self.authed = False
        _log("frånkopplad:", reason)

    def _account_auth(self):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAAccountAuthReq
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.accessToken = ACCESS_TOKEN
        d = self.client.send(req)
        d.addErrback(lambda f: _log("konto-auth-fel:", f))

    def _load_symbols(self):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolsListReq
        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        d = self.client.send(req)
        d.addErrback(lambda f: _log("symbol-list-fel:", f))

    def _load_symbol_details(self):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq
        req = ProtoOASymbolByIdReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.symbolId.append(int(self.symbol_id))
        d = self.client.send(req)
        d.addErrback(lambda f: _log("symbol-detalj-fel:", f))

    def _reconcile(self):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileReq
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        self.client.send(req).addErrback(lambda f: _log("reconcile-fel:", f))

    # ---- meddelanden ----
    def _on_message(self, client, message):
        from ctrader_open_api import Protobuf
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAApplicationAuthRes, ProtoOAAccountAuthRes, ProtoOASymbolsListRes,
            ProtoOASymbolByIdRes, ProtoOAReconcileRes, ProtoOAExecutionEvent,
            ProtoOAErrorRes, ProtoOAOrderErrorEvent)
        try:
            msg = Protobuf.extract(message)
        except Exception:
            return
        t = type(msg)
        if t is ProtoOAApplicationAuthRes:
            _log("app auth OK -> konto-auth")
            self._account_auth()
        elif t is ProtoOAAccountAuthRes:
            self.authed = True
            _log("konto auth OK")
            if self.symbol_id:
                self._load_symbol_details()
            else:
                self._load_symbols()
            self._reconcile()
        elif t is ProtoOASymbolsListRes:
            want = SYMBOL_NAME.strip().lower()
            for s in msg.symbol:
                if s.symbolName.strip().lower() == want:
                    self.symbol_id = s.symbolId
                    break
            if self.symbol_id:
                _log(f"symbol '{SYMBOL_NAME}' -> id {self.symbol_id}")
                self._load_symbol_details()
            else:
                _log(f"HITTADE INTE symbol '{SYMBOL_NAME}' — sätt CT_SYMBOL_NAME/CT_SYMBOL_ID")
        elif t is ProtoOASymbolByIdRes:
            if msg.symbol:
                self.symbol = msg.symbol[0]
                _log(f"symboldetaljer: pip={self.symbol.pipPosition} "
                     f"min={self.symbol.minVolume} step={self.symbol.stepVolume}")
        elif t is ProtoOAReconcileRes:
            self.open_count = len(msg.position)
            _log(f"öppna positioner just nu: {self.open_count}")
        elif t is ProtoOAExecutionEvent:
            self._on_execution(msg)
        elif t in (ProtoOAErrorRes, ProtoOAOrderErrorEvent):
            _log("MÄKLARFEL:", getattr(msg, "description", "") or getattr(msg, "errorCode", ""))

    def _on_execution(self, ev):
        try:
            etype = ev.executionType
        except Exception:
            etype = None
        _log(f"execution-event: type={etype}")
        _append_fill({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "kind": "execution", "type": int(etype) if etype is not None else None})

    # ---- volym / sizing ----
    def _resolve_volume(self, entry, sl):
        """Returnera cTrader-volym (int) eller None om vi inte vågar sizea."""
        if FIXED_VOLUME > 0:
            return FIXED_VOLUME
        if RISK_CCY > 0 and self.symbol is not None:
            # Bäst-möjliga auto-size. VERIFIERA mot en manuell demo-order.
            try:
                dist = abs(float(entry) - float(sl))
                if dist <= 0:
                    return None
                # money per prisenhet per volym-enhet ~ 1/10^pipPosition (grov approx)
                # -> volym = risk / (dist * pipvärde). Vi håller oss konservativa
                #    och rundar till stepVolume, klampar till minVolume.
                pip = 10 ** (-int(self.symbol.pipPosition))
                per_unit = dist / pip                     # antal pips i stoppet
                if per_unit <= 0:
                    return None
                raw = RISK_CCY / per_unit * 100.0         # centi-enheter (grov)
                step = max(1, int(self.symbol.stepVolume))
                vol = int(raw // step) * step
                vol = max(int(self.symbol.minVolume), vol)
                return vol
            except Exception as e:
                _log("auto-size-fel:", e)
                return None
        return None

    # ---- order ----
    def _place(self, rec):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
            ProtoOAOrderType, ProtoOATradeSide)
        side = ProtoOATradeSide.BUY if rec["side"] == "LONG" else ProtoOATradeSide.SELL
        otype = {"MARKET": ProtoOAOrderType.MARKET,
                 "LIMIT":  ProtoOAOrderType.LIMIT,
                 "STOP":   ProtoOAOrderType.STOP}.get(rec.get("order", "MARKET"),
                                                      ProtoOAOrderType.MARKET)
        vol = self._resolve_volume(rec["entry"], rec["sl"])
        if not vol:
            _log(f"HOPPAR {rec['id']}: kan inte fastställa volym "
                 f"(sätt SKILLING_VOLUME eller SKILLING_RISK_CCY + symboldetaljer)")
            return False

        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.symbolId = int(self.symbol_id)
        req.orderType = otype
        req.tradeSide = side
        req.volume = int(vol)
        if otype != ProtoOAOrderType.MARKET and rec.get("limit_price"):
            price = float(rec["limit_price"])
            if otype == ProtoOAOrderType.LIMIT:
                req.limitPrice = price
            else:
                req.stopPrice = price
        if rec.get("sl"):
            req.stopLoss = float(rec["sl"])
        if rec.get("tp1"):
            req.takeProfit = float(rec["tp1"])
        req.label = f"GRABIT-{rec.get('source', 'ROBBER')}"[:50]
        req.comment = f"conf{rec.get('conf')}"[:50]

        summary = (f"{rec['side']} {rec.get('order')} {SYMBOL_NAME} vol={vol} "
                   f"entry={rec.get('limit_price') or rec['entry']} sl={rec['sl']} "
                   f"tp={rec.get('tp1')} [{rec.get('source')} conf {rec.get('conf')}]")
        if DRY_RUN:
            _log("DRY_RUN — skulle lägga:", summary)
            _append_fill({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "kind": "dry_run", "id": rec["id"], "summary": summary})
            return True

        _log("LÄGGER ORDER:", summary)
        d = self.client.send(req)
        d.addErrback(lambda f: _log("order-fel:", f))
        _append_fill({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "kind": "order_sent", "id": rec["id"], "summary": summary})
        return True

    # ---- pollning ----
    def _poll(self):
        if not self.authed:
            return
        st = self.state
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if st.get("day") != day:
            st["day"] = day
            st["halted"] = False           # ny dag -> nollställ kill-switch-flaggan
            _save_state(st)
        if st.get("halted"):
            return
        for rec in _read_new_signals(st):
            rid = rec["id"]
            # filter
            if ONLY_SOURCES and rec.get("source") not in ONLY_SOURCES:
                st.setdefault("processed", []).append(rid); continue
            if _too_old(rec):
                _log(f"hoppar {rid}: för gammal signal (> {MAX_SIGNAL_AGE}s)")
                st.setdefault("processed", []).append(rid); continue
            if self.open_count >= MAX_OPEN:
                _log(f"hoppar {rid}: MAX_OPEN={MAX_OPEN} redan öppna")
                st.setdefault("processed", []).append(rid); continue
            ok = False
            try:
                ok = self._place(rec)
            except Exception as e:
                _log(f"place-fel {rid}:", e)
            if ok:
                self.open_count += 1
            st.setdefault("processed", []).append(rid)
            _save_state(st)
        # håll processed-listan lagom
        if len(st.get("processed", [])) > 500:
            st["processed"] = st["processed"][-500:]
            _save_state(st)
        self._reconcile()


def _dry_only_loop():
    """Utan SDK/creds: läs kön och logga tänkta ordrar. Nollrisk-test av flödet."""
    _log("SDK/creds saknas eller CT_DRY_ONLY=1 — kör ren kö-loggning (ingen mäklare).")
    state = _load_state()
    while True:
        for rec in _read_new_signals(state):
            _log("DRY (kö) — skulle lägga:",
                 f"{rec.get('side')} {rec.get('order')} {rec.get('ticker')} "
                 f"entry={rec.get('limit_price') or rec.get('entry')} sl={rec.get('sl')} "
                 f"tp={rec.get('tp1')} [{rec.get('source')} conf {rec.get('conf')}]")
            state.setdefault("processed", []).append(rec["id"])
            _save_state(state)
        time.sleep(POLL_SEC)


def main():
    missing = [k for k, v in {
        "CT_CLIENT_ID": CLIENT_ID, "CT_CLIENT_SECRET": CLIENT_SECRET,
        "CT_ACCESS_TOKEN": ACCESS_TOKEN, "CT_ACCOUNT_ID": ACCOUNT_ID}.items() if not v]
    if os.environ.get("CT_DRY_ONLY") == "1" or missing:
        if missing:
            _log("saknar env:", ", ".join(missing), "-> kör kö-loggning i stället.")
        return _dry_only_loop()
    try:
        CTraderExecutor().start()
    except ImportError:
        _log("ctrader-open-api saknas (pip install ctrader-open-api) -> kö-loggning.")
        _dry_only_loop()


if __name__ == "__main__":
    main()
