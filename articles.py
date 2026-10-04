"""
GRABIT  ·  articles.py
----------------------
Publika analyser på grabitlabs.com/analys. Artikeln är gratis för alla
(bolaget, siffrorna, nyheterna, riskerna) – Grabits betyg, nivåer och
slutsats finns bara i appen, bakom registrering/PRO. Därför innehåller
artikelfilerna aldrig score, verdict eller "Tesen": det som inte står i
filen kan inte läcka i sidkällan.

En artikel = en JSON-fil i articles/ (se oracle-oktober-2026.json).
Varje siffra ska ha en källa i "kallor".
"""

import os
import json
import html

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "articles")
_SITE = "https://grabitlabs.com"


def _load_all():
    out = []
    try:
        for fn in os.listdir(_DIR):
            if fn.endswith(".json"):
                with open(os.path.join(_DIR, fn), encoding="utf-8") as f:
                    out.append(json.load(f))
    except Exception:
        pass
    out.sort(key=lambda a: a.get("datum", ""), reverse=True)
    return out


def latest():
    a = _load_all()
    return a[0] if a else None


def get(slug):
    for a in _load_all():
        if a.get("slug") == slug:
            return a
    return None


def _e(s):
    return html.escape(str(s or ""), quote=True)


_CSS = """<style>
:root{--bg:#020305;--card:rgba(14,22,30,.8);--line:rgba(120,230,255,.12);--txt:#e8edf5;--mut:rgba(232,237,245,.62);
--neon:#00e5ff;--gold:#FFC940}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--txt);font-family:"Space Grotesk",system-ui,-apple-system,Segoe UI,Roboto,sans-serif;line-height:1.65;-webkit-font-smoothing:antialiased}
a{color:var(--neon)}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;max-width:760px;margin:0 auto;padding:18px 16px}
.top img{height:22px;display:block}.top nav{display:flex;gap:8px;align-items:center}
.lang button{background:none;border:1px solid var(--line);color:var(--mut);border-radius:8px;padding:4px 9px;font:inherit;font-size:12px;cursor:pointer}
.lang button.on{color:var(--txt);border-color:var(--neon)}
.cta-s{background:var(--neon);color:#001116;text-decoration:none;font-weight:700;font-size:13px;padding:7px 12px;border-radius:9px}
main{max-width:760px;margin:0 auto;padding:6px 16px 60px}
.kick{color:var(--gold);font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}
h1{font-size:clamp(28px,6vw,44px);line-height:1.12;margin:10px 0 12px}
.ing{font-size:19px;color:var(--txt);opacity:.9;margin:0 0 10px}
.meta{color:var(--mut);font-size:13px;margin-bottom:22px}
.sum{font-size:17px}
h2{font-size:21px;margin:34px 0 8px;color:#fff}
p{margin:0 0 14px}
.kt{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:26px 0}
.kt div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.kt b{display:block;font-size:17px}.kt span{color:var(--mut);font-size:12px}
.lock{position:relative;margin:38px 0;border:1px solid rgba(255,201,64,.4);border-radius:16px;padding:22px;background:linear-gradient(180deg,rgba(255,201,64,.07),rgba(255,201,64,.02))}
.lock h3{margin:0 0 6px;font-size:20px;color:var(--gold)}
.lock ul{margin:12px 0 18px;padding-left:18px;color:var(--mut)}
.lock .ghost{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 16px}
.lock .ghost span{filter:blur(5px);user-select:none;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-weight:700}
.btn{display:inline-block;background:var(--neon);color:#001116;font-weight:800;text-decoration:none;padding:13px 20px;border-radius:12px}
.src{margin-top:40px;font-size:13px;color:var(--mut)}.src ol{padding-left:18px}.src li{margin-bottom:4px}
.disc{margin-top:26px;font-size:12px;color:var(--mut);border-top:1px solid var(--line);padding-top:14px}
hr{border:0;border-top:1px solid rgba(255,201,64,.35);margin:38px 0 6px}
.big{margin:18px 0;padding:16px 18px;border-left:3px solid var(--gold);background:rgba(255,201,64,.05);border-radius:0 12px 12px 0}
.big b{display:block;font-size:clamp(34px,9vw,52px);line-height:1.05;color:var(--gold)}.big span{color:var(--mut);font-size:14px}
.strong{font-size:20px;font-weight:700;color:#fff;letter-spacing:.01em;margin:18px 0}
.bl{padding-left:20px;margin:0 0 16px}.bl li{margin:4px 0}
.flow{display:flex;flex-direction:column;align-items:flex-start;gap:2px;margin:14px 0 18px}
.flow span{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:7px 12px;font-weight:600}
.flow i{font-style:normal;color:var(--neon);padding-left:14px}
.kt em{display:block;font-style:normal;color:var(--neon);font-weight:700;font-size:13px;margin-top:2px}
.hero{margin:0 0 22px}.hero img{width:100%;height:auto;display:block;border-radius:14px;border:1px solid var(--line)}
.hero figcaption{color:var(--mut);font-size:12px;margin-top:6px}
.L{display:none}.L.on{display:block}
.list a{display:block;text-decoration:none;color:var(--txt);background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
.list a b{display:block;font-size:19px;margin:4px 0}.list a span{color:var(--mut);font-size:14px}
</style>"""

_FONT = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">')

_LANG_JS = """<script>
function setL(l){document.documentElement.lang=l;
document.querySelectorAll('.L').forEach(e=>e.classList.toggle('on',e.classList.contains('L-'+l)));
document.querySelectorAll('.lang button').forEach(b=>b.classList.toggle('on',b.dataset.l===l));
try{localStorage.setItem('grabit_lang',l)}catch(e){}}
(function(){var s=null;try{s=localStorage.getItem('grabit_lang')}catch(e){}
setL(s||((navigator.language||'sv').toLowerCase().indexOf('sv')===0?'sv':'en'))})();
</script>"""


def _head(title, desc, url, extra="", image=None):
    return ('<!doctype html><html lang="sv"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><meta name="description" content="%s">'
            '<link rel="canonical" href="%s"><link rel="icon" href="/logo.png">'
            '<meta property="og:type" content="article"><meta property="og:title" content="%s">'
            '<meta property="og:description" content="%s"><meta property="og:url" content="%s">'
            '<meta property="og:image" content="%s"><meta name="twitter:card" content="summary_large_image">'
            '%s%s%s</head><body>' % (_e(title), _e(desc), url, _e(title), _e(desc), url,
                                         image or (_SITE + "/grabit_wordmark.png"), _FONT, _CSS, extra))


def _top():
    return ('<div class="top"><a href="/start"><img src="/grabit_wordmark.png" alt="GRABIT"></a><nav>'
            '<span class="lang"><button data-l="sv" onclick="setL(\'sv\')">SV</button>'
            '<button data-l="en" onclick="setL(\'en\')">EN</button></span>'
            '<a class="cta-s" href="/?pw=1&amp;go=1"><span class="L L-sv">Prova gratis</span>'
            '<span class="L L-en">Try free</span></a></nav></div>')


def _hero(a, en):
    if not a.get("bild"):
        return ""
    cap = "Illustration: GRABIT (AI-generated image)" if en else "Illustration: GRABIT (AI-genererad bild)"
    return ('<figure class="hero"><img src="/analys/bild/%s" alt="%s" loading="eager">'
            '<figcaption>%s</figcaption></figure>') % (_e(a["bild"]), _e(a.get("titel_en" if en else "titel")), cap)


def image_path(name):
    """Sökväg till en artikelbild, eller None. Bara filnamn ur articles/, inga ../"""
    name = os.path.basename(name or "")
    if not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        return None
    p = os.path.join(_DIR, name)
    return p if os.path.isfile(p) else None


def _blocks(blocks):
    """Artikeltext i block: h, p, big (+l), strong, list, flow, stats, hr."""
    out = []
    for b in blocks or []:
        if "h" in b:
            out.append("<h2>%s</h2>" % _e(b["h"]))
        elif "p" in b:
            out.append("<p>%s</p>" % _e(b["p"]))
        elif "big" in b:
            out.append('<div class="big"><b>%s</b><span>%s</span></div>' % (_e(b["big"]), _e(b.get("l"))))
        elif "strong" in b:
            out.append('<p class="strong">%s</p>' % _e(b["strong"]))
        elif "list" in b:
            out.append('<ul class="bl">%s</ul>' % "".join("<li>%s</li>" % _e(x) for x in b["list"]))
        elif "flow" in b:
            out.append('<div class="flow">%s</div>' % '<i>↓</i>'.join("<span>%s</span>" % _e(x) for x in b["flow"]))
        elif "stats" in b:
            out.append('<div class="kt">%s</div>' % "".join(
                "<div><b>%s</b><span>%s</span>%s</div>" % (_e(x["v"]), _e(x.get("l")),
                                                          ('<em>%s</em>' % _e(x["d"])) if x.get("d") else "")
                for x in b["stats"]))
        elif "hr" in b:
            out.append('<hr>')
    return "".join(out)


def _body(a, en):
    k = "_en" if en else ""
    if a.get("block" + k):
        secs = _blocks(a.get("block" + k))
    else:
        secs = "".join("<h2>%s</h2><p>%s</p>" % (_e(s["h"]), _e(s["t"])) for s in a.get("sektioner" + k) or [])
    kt = "".join("<div><b>%s</b><span>%s</span></div>" % (_e(x["v"]), _e(x["k"])) for x in a.get("nyckeltal" + k) or [])
    tk = _e(a.get("ticker"))
    if en:
        lock = ('<div class="lock"><h3>Levels &amp; score for %s</h3>'
                '<p>This is %s. The analysis above is free – our levels, score and verdict are for PRO members:</p>'
                '<div class="ghost"><span>Score 0.0/10</span><span>Verdict ●●●</span><span>Entry $000</span><span>Stop $000</span></div>'
                '<ul><li>Grabit score and verdict</li><li>Entry, stop and target levels</li>'
                '<li>Our full thesis — and alerts when the setup changes</li></ul><p style="font-size:13px;color:var(--mut)">Not a member? 7 days free, cancel anytime.</p>'
                '<a class="btn" href="/?case=1">See Grabit\'s levels – open the case in PRO →</a></div>') % (tk, _e(a.get("serie_en") or "monthly case"))
        meta = "%s · %s" % (_e(a.get("serie_en") or "Monthly case"), _e(a.get("datum")))
        src_h, disc = "Sources", ("Not financial advice. Information only — do your own research. "
                                  "Figures as reported by the company.")
    else:
        lock = ('<div class="lock"><h3>Nivåer &amp; betyg för %s</h3>'
                '<p>Det här är %s. Analysen ovan är gratis – våra nivåer, betyg och slutsats finns för PRO-medlemmar:</p>'
                '<div class="ghost"><span>Betyg 0,0/10</span><span>Slutsats ●●●</span><span>Entry $000</span><span>Stopp $000</span></div>'
                '<ul><li>Grabit-betyg och slutsats</li><li>Nivåer för entry, stopp och mål</li>'
                '<li>Hela vår tes – och larm när läget ändras</li></ul><p style="font-size:13px;color:var(--mut)">Inte medlem? 7 dagar gratis, avsluta när du vill.</p>'
                '<a class="btn" href="/?case=1">Se Grabits nivåer – öppna caset i PRO →</a></div>') % (tk, _e(a.get("serie") or "månadens case"))
        meta = "%s · %s" % (_e(a.get("serie") or "Analys"), _e(a.get("datum")))
        src_h, disc = "Källor", ("Inte finansiell rådgivning. Endast information – gör din egen analys. "
                                 "Siffror enligt bolaget.")
    src = "".join('<li><a href="%s" target="_blank" rel="noopener">%s</a></li>' % (_e(s["u"]), _e(s["t"]))
                  for s in a.get("kallor") or [])
    return ('<div class="L L-%s"><div class="kick">%s · %s</div><h1>%s</h1><p class="ing">%s</p>'
            '<div class="meta">%s</div>%s%s%s%s%s'
            '%s<div class="disc">%s</div></div>') % (
        "en" if en else "sv", _e(a.get("kick")) or tk, _e(a.get("bolag")), _e(a.get("titel" + k)), _e(a.get("ingress" + k)),
        meta, _hero(a, en),
        ('<p class="sum">%s</p>' % _e(a["sammanfattning" + k])) if a.get("sammanfattning" + k) else "",
        ('<div class="kt">%s</div>' % kt) if kt else "", secs, lock, "", disc)


def render_article(a, embed=False):
    url = "%s/analys/%s" % (_SITE, a["slug"])
    ld = json.dumps({"@context": "https://schema.org", "@type": "Article", "headline": a.get("titel"),
                     "description": a.get("ingress"), "datePublished": a.get("datum"),
                     "publisher": {"@type": "Organization", "name": "GRABIT"}, "url": url}, ensure_ascii=False)
    extra = '<script type="application/ld+json">%s</script>' % ld.replace("</", "<\\/")
    img = ("%s/analys/bild/%s" % (_SITE, a["bild"])) if a.get("bild") else None
    if embed:
        extra += '<base target="_top"><style>main{padding-top:18px}</style>'
    return (_head("%s | GRABIT" % a.get("titel"), a.get("ingress"), url, extra, img) + ("" if embed else _top())
            + "<main>" + _body(a, False) + _body(a, True) + "</main>" + _LANG_JS + "</body></html>")


def render_index():
    arts = _load_all()

    def items(en):
        k = "_en" if en else ""
        return "".join('<a href="/analys/%s"><span>%s · %s</span><b>%s</b><span>%s</span></a>' % (
            _e(a["slug"]), _e(a.get("ticker")), _e(a.get("datum")), _e(a.get("titel" + k)), _e(a.get("ingress" + k)))
            for a in arts)
    body = ('<main><div class="L L-sv"><div class="kick">Analyser</div><h1>Djupdykningar i bolag</h1>'
            '<p class="ing">Gratis att läsa. Nivåer och vår slutsats finns i appen.</p><div class="list">%s</div></div>'
            '<div class="L L-en"><div class="kick">Analysis</div><h1>Company deep dives</h1>'
            '<p class="ing">Free to read. Levels and our verdict are in the app.</p><div class="list">%s</div></div></main>'
            ) % (items(False), items(True))
    return (_head("Analyser | GRABIT", "Djupdykningar i bolag från GRABIT.", _SITE + "/analys")
            + _top() + body + _LANG_JS + "</body></html>")
