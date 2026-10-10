"""
GRABIT  ·  stock_pages.py
-------------------------
Publika aktiesidor (/aktie/TKR) som Google kan hitta. Varje sida visar det
som är gratis: kurs, utveckling, vad bolaget gör och våra artiklar om bolaget.
Grabits betyg, etikett och nivåer finns bara i appen – de skrivs aldrig in i
sidan, så de kan inte läcka i sidkällan.

All data kommer från det som redan ligger i minnet (skannerns rader och de
sparade bolagsbeskrivningarna). En sidvisning gör alltså inga egna anrop mot
kursdata eller AI, och robotar som läser alla sidor kostar nästan ingenting.
"""

import json
import re

from articles import _e, _head, _top, _LANG_JS, _SITE

_TK_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")

_CSS_EXTRA = """<style>
.kt b.up{color:#2fe39a}.kt b.dn{color:#ff6b7d}
.rel{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 6px}
.rel a{text-decoration:none;color:var(--txt);background:var(--card);border:1px solid var(--line);border-radius:10px;padding:7px 11px;font-weight:600;font-size:14px}
.rel a:hover{border-color:var(--neon)}
.arts a{display:block;text-decoration:none;color:var(--txt);background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:8px 0}
.arts a b{display:block}.arts a span{color:var(--mut);font-size:13px}
.crumb{color:var(--mut);font-size:13px;margin-bottom:10px}.crumb a{color:var(--mut)}
.note{color:var(--mut);font-size:12px;margin-top:-14px}
.grp h2{margin-top:26px}
</style>"""


def valid_ticker(tk):
    return bool(_TK_RE.match(tk or ""))


def _pct(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v


def _fmt_pct(v, en):
    s = ("%+.1f %%" % v) if not en else ("%+.1f%%" % v)
    return s if en else s.replace(".", ",")


def _fmt_price(v, tk, en):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "–"
    sek = tk.endswith(".ST")
    txt = ("%.2f" % v) if v < 1000 else ("{:,.0f}".format(v))
    if not en:
        txt = txt.replace(",", " ").replace(".", ",")
    return (txt + " kr") if sek else ("$" + txt)


def _fmt_mcap(musd, en):
    try:
        m = float(musd)
    except (TypeError, ValueError):
        return None
    if m <= 0:
        return None
    if m >= 1000:
        s = "%.1f" % (m / 1000)
        return ("$%s bn" % s) if en else ("%s md USD" % s.replace(".", ","))
    s = "%.0f" % m
    return ("$%sM" % s) if en else ("%s MUSD" % s)


def _stats(row, tk, en):
    if not row:
        return ""
    out = []

    def add(v, label, cls=""):
        out.append('<div><b class="%s">%s</b><span>%s</span></div>' % (cls, _e(v), _e(label)))

    if row.get("last") is not None:
        add(_fmt_price(row.get("last"), tk, en), "Last price" if en else "Senaste kurs")
    for key, sv, en_l in (("ret_1", "Idag", "Today"), ("ret_5", "5 dagar", "5 days"),
                          ("ret_20", "1 månad", "1 month")):
        v = _pct(row.get(key))
        if v is not None:
            add(_fmt_pct(v, en), en_l if en else sv, "up" if v >= 0 else "dn")
    v = _pct(row.get("pct_from_high"))
    if v is not None:
        add(_fmt_pct(v, en), "From 52-week high" if en else "Från 52-veckorshögsta")
    m = _fmt_mcap(row.get("mcap_musd"), en)
    if m:
        add(m, "Market cap" if en else "Börsvärde")
    return '<div class="kt">%s</div>' % "".join(out)


def _body(tk, name, theme, row, blurb, arts, related, en):
    k = "_en" if en else ""
    lang = "en" if en else "sv"
    h1 = ("%s (%s) stock" % (name, tk)) if (en and name != tk) else (
        ("%s stock" % tk) if en else (("%s (%s) aktie" % (name, tk)) if name != tk else "%s aktie" % tk))
    ing = (("Price, performance and what %s does – plus Grabit's levels and score in the app." % name) if en
           else ("Kurs, utveckling och vad %s gör – plus Grabits nivåer och betyg i appen." % name))
    parts = ['<div class="L L-%s">' % lang,
             '<div class="crumb"><a href="/aktier">%s</a> · %s</div>' % ("Stocks" if en else "Aktier", _e(theme or "")),
             '<div class="kick">%s%s</div>' % ("Stock" if en else "Aktie", (" · " + _e(theme)) if theme else ""),
             '<h1>%s</h1><p class="ing">%s</p>' % (_e(h1), _e(ing)),
             _stats(row, tk, en)]
    if row:
        parts.append('<p class="note">%s</p>' % (
            "Delayed prices, updated during the day." if en else "Fördröjda kurser, uppdateras under dagen."))
    if blurb and blurb.get("summary"):
        parts.append('<h2>%s</h2><p>%s</p>' % (_e(("About %s" if en else "Om %s") % name), _e(blurb["summary"])))
        if blurb.get("sector"):
            parts.append('<p style="color:var(--mut);font-size:14px">%s: %s</p>' % (
                "Sector" if en else "Sektor", _e(blurb["sector"])))
    parts.append('<h2>%s</h2><p>%s</p>' % (
        _e(("Why is %s moving today?" if en else "Varför rör sig %s idag?") % tk),
        _e(("Grabit explains the move – news, filings and volume – on the stock card in the app."
            if en else "Grabit förklarar rörelsen – nyheter, rapporter och volym – på aktiekortet i appen."))))
    parts.append(
        '<div class="lock"><h3>%s</h3><p>%s</p>'
        '<div class="ghost"><span>%s 0.0/10</span><span>%s ●●●</span><span>Entry $000</span><span>%s $000</span></div>'
        '<ul>%s</ul><div class="lbtns"><a class="btn" href="/?stock=%s">%s</a></div>'
        '<p style="font-size:13px;color:var(--mut);margin:12px 0 0">%s</p></div>' % (
            _e(("Grabit's levels & score for %s" if en else "Grabits nivåer & betyg för %s") % tk),
            _e("The analysis above is free. Our score, setup and levels are in the app:" if en
               else "Det ovan är gratis. Vårt betyg, läge och nivåer finns i appen:"),
            "Score" if en else "Betyg", "Setup" if en else "Läge", "Stop" if en else "Stopp",
            "".join("<li>%s</li>" % _e(x) for x in (
                ("Grabit score and setup label", "Entry, stop and target levels", "Alerts when the setup changes")
                if en else ("Grabit-betyg och läge", "Nivåer för entry, stopp och mål", "Larm när läget ändras"))),
            _e(tk), _e(("Open %s in the app →" if en else "Öppna %s i appen →") % tk),
            _e("Not a member? 7 days free, cancel anytime." if en else "Inte medlem? 7 dagar gratis, avsluta när du vill.")))
    if arts:
        parts.append('<h2>%s</h2><div class="arts">%s</div>' % (
            "Grabit's analysis" if en else "Grabits analyser",
            "".join('<a href="/analys/%s"><b>%s</b><span>%s</span></a>' % (
                _e(a["slug"]), _e(a.get("titel" + k) or a.get("titel")), _e(a.get("ingress" + k) or a.get("ingress")))
                for a in arts)))
    if related:
        parts.append('<h2>%s</h2><div class="rel">%s</div>' % (
            _e(("More in %s" if en else "Fler inom %s") % theme),
            "".join('<a href="/aktie/%s">%s</a>' % (_e(t), _e(t)) for t in related)))
    parts.append('<div class="disc">%s</div></div>' % _e(
        "Not financial advice. Information only — do your own research." if en
        else "Inte finansiell rådgivning. Endast information – gör din egen analys."))
    return "".join(parts)


def render_stock(tk, row, blurb_sv, blurb_en, theme, related, arts):
    name = ((row or {}).get("name") or (blurb_sv or {}).get("name") or (blurb_en or {}).get("name") or tk).strip()
    url = "%s/aktie/%s" % (_SITE, tk)
    title = ("%s aktie – kurs, analys och nivåer | GRABIT" % tk) if name == tk else (
        "%s (%s) aktie – kurs, analys och nivåer | GRABIT" % (name, tk))
    desc = ((blurb_sv or {}).get("summary") or
            ("Kurs, utveckling och Grabits analys av %s (%s)." % (name, tk)))[:300]
    ld = json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Aktier", "item": _SITE + "/aktier"},
        {"@type": "ListItem", "position": 2, "name": "%s (%s)" % (name, tk), "item": url}]}, ensure_ascii=False)
    extra = '<script type="application/ld+json">%s</script>%s' % (ld.replace("</", "<\\/"), _CSS_EXTRA)
    return (_head(title, desc, url, extra) + _top() + "<main>"
            + _body(tk, name, theme, row, blurb_sv, arts, related, False)
            + _body(tk, name, theme, row, blurb_en or blurb_sv, arts, related, True)
            + "</main>" + _LANG_JS + "</body></html>")


def render_index(groups, names):
    """groups: [(tema, [tickers])], names: {ticker: namn}"""
    def links(ts):
        return "".join('<a href="/aktie/%s" title="%s">%s</a>' % (_e(t), _e(names.get(t, "")), _e(t)) for t in ts)
    secs = "".join('<div class="grp"><h2>%s</h2><div class="rel">%s</div></div>' % (_e(g), links(ts)) for g, ts in groups)
    body = ('<main><div class="L L-sv"><div class="kick">Aktier</div><h1>Aktier som Grabit bevakar</h1>'
            '<p class="ing">Kurs, utveckling och vad bolagen gör – gratis. Nivåer och betyg finns i appen.</p>%s</div>'
            '<div class="L L-en"><div class="kick">Stocks</div><h1>Stocks Grabit covers</h1>'
            '<p class="ing">Price, performance and what the companies do – free. Levels and scores are in the app.</p>%s</div></main>'
            ) % (secs, secs)
    return (_head("Aktier – kurs och analys | GRABIT", "Alla aktier som Grabit bevakar: kurs, utveckling och bolagsfakta.",
                  _SITE + "/aktier", _CSS_EXTRA) + _top() + body + _LANG_JS + "</body></html>")


def sitemap(tickers, slugs):
    urls = ["/start", "/analys", "/aktier"] + ["/analys/" + s for s in slugs] + ["/aktie/" + t for t in tickers]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join("<url><loc>%s%s</loc></url>\n" % (_SITE, _e(u)) for u in urls) + "</urlset>\n")


def robots():
    return "User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: %s/sitemap.xml\n" % _SITE
