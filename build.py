"""Build static site from articles/ + products.json into public/.

Run: python build.py
"""
import json, re, shutil, html
from pathlib import Path
from datetime import date, timedelta
import random

ROOT = Path(__file__).parent
PUB = ROOT / "public"
CATALOG = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
SITE = CATALOG["site"]
PRODUCTS = CATALOG["products"]
CATS = CATALOG["categories"]
PRODS = {p["asin"]: p for p in PRODUCTS}
BASE = SITE["url"].rstrip("/")

# Paste Google Search Console verification code here (content= value), then rebuild+deploy
GSC_VERIFICATION = "rExqKrlIUHf3C8lAuDKtsLeQVUqIIZ_IaHXKvClNwrQ"
INDEXNOW_KEY = "8f3a1c9e62b74d05a4e8c7f2d1b09a36"


def extract_faq(body_html):
    """Pull Q/A pairs out of the article's FAQ section for FAQPage schema."""
    m = re.search(r"<h2>\s*FAQ\s*</h2>(.*)$", body_html, re.S | re.I)
    if not m:
        return []
    section = m.group(1)
    pairs = re.findall(r"<h3>(.*?)</h3>\s*(.*?)(?=<h3>|$)", section, re.S)
    out = []
    for q, a in pairs:
        q = re.sub(r"<[^>]+>", "", q).strip()
        a = re.sub(r"<[^>]+>", " ", a)
        a = re.sub(r"\s+", " ", a).strip()
        if q and a:
            out.append((q, a[:1000]))
    return out[:8]

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--ink:#1f2733;--sub:#5b6472;--bg:#faf9f6;--card:#fff;--accent:#e05d3d;--accent2:#2a7f62;--line:#e8e4dc}
body{font-family:Georgia,'Times New Roman',serif;color:var(--ink);background:var(--bg);line-height:1.7;font-size:18px}
header{background:#fff;border-bottom:1px solid var(--line)}
.nav{max-width:1060px;margin:0 auto;padding:14px 20px;display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.logo{font-size:1.35rem;font-weight:700;color:var(--ink);text-decoration:none;font-family:Verdana,sans-serif;letter-spacing:-.5px}
.logo span{color:var(--accent)}
.nav a{color:var(--sub);text-decoration:none;font-family:Verdana,sans-serif;font-size:.82rem}
.nav a:hover{color:var(--accent)}
main{max-width:1060px;margin:0 auto;padding:34px 20px}
h1{font-size:2rem;line-height:1.25;margin-bottom:14px}
h2{font-size:1.4rem;margin:30px 0 12px}
h3{font-size:1.1rem;margin:20px 0 8px}
p{margin-bottom:16px}
ul,ol{margin:0 0 16px 26px}
li{margin-bottom:6px}
a{color:var(--accent2)}
.sub{color:var(--sub);font-size:.95rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px;margin:24px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;transition:box-shadow .15s}
.card:hover{box-shadow:0 4px 14px rgba(0,0,0,.07)}
.card a.t{font-family:Verdana,sans-serif;font-size:.95rem;font-weight:700;color:var(--ink);text-decoration:none;display:block;margin-bottom:8px;line-height:1.4}
.card a.t:hover{color:var(--accent)}
.card p{font-size:.88rem;color:var(--sub);margin:0}
.tag{display:inline-block;font-family:Verdana,sans-serif;font-size:.68rem;text-transform:uppercase;letter-spacing:.8px;color:var(--accent);margin-bottom:8px}
.pbox{display:flex;gap:20px;background:#fff;border:2px solid var(--accent);border-radius:12px;padding:20px;margin:28px 0;align-items:center;flex-wrap:wrap}
.pbox img{width:150px;height:150px;object-fit:contain;flex-shrink:0;background:#fff}
.pbox .pb-body{flex:1;min-width:220px}
.pbox .pb-name{font-family:Verdana,sans-serif;font-weight:700;font-size:1.02rem;margin-bottom:6px}
.pbox .pb-desc{font-size:.9rem;color:var(--sub);margin-bottom:12px}
.btn{display:inline-block;background:var(--accent);color:#fff;font-family:Verdana,sans-serif;font-weight:700;font-size:.9rem;padding:11px 22px;border-radius:7px;text-decoration:none}
.btn:hover{background:#c74d30}
.pcard{text-align:center}
.pcard img{width:100%;height:190px;object-fit:contain;margin-bottom:12px;background:#fff}
.pemoji{display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#fff3ea,#ffe6d6);border-radius:10px;line-height:1}
.pemoji-box{width:150px;height:150px;font-size:4rem;flex-shrink:0}
.pemoji-card{width:100%;height:190px;font-size:5rem;margin-bottom:12px}
.hero{background:linear-gradient(135deg,#2a7f62 0%,#1f5d48 100%);color:#fff;border-radius:14px;padding:44px 34px;margin-bottom:34px}
.hero h1{color:#fff;font-size:2.2rem}
.hero p{color:#d8ece4;max-width:640px;font-size:1.05rem}
.crumbs{font-family:Verdana,sans-serif;font-size:.75rem;color:var(--sub);margin-bottom:18px}
.crumbs a{color:var(--sub)}
article img{max-width:100%}
.disclosure{font-size:.8rem;color:var(--sub);background:#f3f1ec;border-radius:8px;padding:10px 14px;margin:18px 0;font-family:Verdana,sans-serif}
footer{border-top:1px solid var(--line);margin-top:60px;background:#fff}
.foot{max-width:1060px;margin:0 auto;padding:30px 20px;font-family:Verdana,sans-serif;font-size:.78rem;color:var(--sub)}
.foot a{color:var(--sub);margin-right:16px}
.related{margin-top:44px}
@media(max-width:640px){h1{font-size:1.5rem}.hero h1{font-size:1.6rem}body{font-size:17px}}
"""

DISCLOSURE = '<div class="disclosure">This post contains affiliate links. As an Amazon Associate, Kitchen Gadget Grove earns from qualifying purchases at no extra cost to you.</div>'


def esc(s):
    return html.escape(s or "", quote=True)


def page(title, description, canonical, body, jsonld=None, noindex=False, image=None):
    gsc = f'<meta name="google-site-verification" content="{GSC_VERIFICATION}">' if GSC_VERIFICATION else ""
    robots = '<meta name="robots" content="noindex">' if noindex else ""
    ld = ""
    if jsonld:
        ld = "".join(f'<script type="application/ld+json">{json.dumps(j, ensure_ascii=False)}</script>' for j in jsonld)
    og_img = f'<meta property="og:image" content="{esc(image)}"><meta name="twitter:image" content="{esc(image)}">' if image else ""
    tw_card = "summary_large_image" if image else "summary"
    nav_cats = "".join(f'<a href="/category/{c}/">{esc(n)}</a>' for c, n in CATS.items())
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{canonical}">
{gsc}{robots}
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{canonical}">
<meta property="og:site_name" content="{esc(SITE['name'])}">
{og_img}
<meta name="twitter:card" content="{tw_card}">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE['name'])}" href="/feed.xml">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🍳</text></svg>">
{ld}
<style>{CSS}</style>
</head>
<body>
<header><nav class="nav"><a class="logo" href="/">Kitchen<span>Gadget</span>Grove</a>{nav_cats}</nav></header>
<main>{body}</main>
<footer><div class="foot">
<p style="margin-bottom:10px"><a href="/">Home</a><a href="/about/">About</a><a href="/affiliate-disclosure/">Affiliate Disclosure</a></p>
<p>As an Amazon Associate we earn from qualifying purchases. &copy; 2026 {esc(SITE['name'])}. Product prices and availability are accurate as of the date/time indicated on Amazon and are subject to change.</p>
</div></footer>
</body></html>"""


PEMOJI = {"party": "\U0001F389", "baking": "\U0001F35E", "bbq": "\U0001F356",
          "cooking": "\U0001F373", "kitchen": "\U0001F52A", "fun": "\U0001F381"}


def prod_img(p, box=False):
    """Real product photo if present, else a styled emoji tile (no fabricated image URLs)."""
    if p.get("image"):
        if box:
            return f'<img src="{esc(p["image"])}" alt="{esc(p["name"])}" loading="lazy" width="150" height="150">'
        return f'<img src="{esc(p["image"])}" alt="{esc(p["name"])}" loading="lazy">'
    em = p.get("emoji") or PEMOJI.get(p.get("category"), "\U0001F37D")
    cls = "pemoji pemoji-box" if box else "pemoji pemoji-card"
    return f'<div class="{cls}" role="img" aria-label="{esc(p["name"])}">{em}</div>'


def product_box(p, heading=None):
    h = f'<div class="tag">{esc(heading)}</div>' if heading else ""
    return f"""<div class="pbox">{h}
{prod_img(p, box=True)}
<div class="pb-body">
<div class="pb-name">{esc(p['name'])}</div>
<div class="pb-desc">{esc(p['short'])}</div>
<a class="btn" href="{esc(p['url'])}" rel="sponsored nofollow noopener" target="_blank">Check Price on Amazon &rarr;</a>
</div></div>"""


def article_card(a):
    d = a["meta_description"]
    cat = PRODS[a["product"]]["category"] if a.get("product") else "party"
    return f"""<div class="card"><span class="tag">{esc(CATS.get(cat, 'Guide'))}</span>
<a class="t" href="/articles/{a['slug']}/">{esc(a['title'])}</a>
<p>{esc(d[:120])}&hellip;</p></div>"""


def write(path, content):
    f = PUB / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")


def main():
    if PUB.exists():
        shutil.rmtree(PUB)
    PUB.mkdir()

    arts = []
    for f in sorted((ROOT / "articles").glob("*.json")):
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"skipping unreadable {f.name}")
            continue
        if a.get("html") and a.get("title"):
            arts.append(a)
    print(f"{len(arts)} articles loaded")

    rnd = random.Random(42)
    # stable pseudo publish dates spread over last 60 days (freshness spread for GSC)
    start = date.today() - timedelta(days=60)
    for i, a in enumerate(sorted(arts, key=lambda x: x["slug"])):
        a["date"] = (start + timedelta(days=(i * 60) // max(1, len(arts)))).isoformat()

    by_product = {}
    for a in arts:
        if a.get("product"):
            by_product.setdefault(a["product"], []).append(a)
    roundups = [a for a in arts if a["type"] == "roundup"]

    urls = []

    # ---------- article pages ----------
    for a in arts:
        p = PRODS.get(a["product"]) if a.get("product") else None
        canonical = f"{BASE}/articles/{a['slug']}/"
        # related: same product first, then random
        pool = [x for x in (by_product.get(a["product"], []) if p else roundups) if x["slug"] != a["slug"]]
        extra = [x for x in arts if x["slug"] != a["slug"] and x not in pool]
        rnd.shuffle(pool); rnd.shuffle(extra)
        related = (pool + extra)[:4]
        rel_html = '<div class="related"><h2>Keep Reading</h2><div class="grid">' + "".join(article_card(x) for x in related) + "</div></div>"

        body_html = a["html"]
        # insert product box after 2nd </p> (or roundup: all relevant boxes at end)
        if p:
            parts = body_html.split("</p>")
            if len(parts) > 3:
                body_html = "</p>".join(parts[:2]) + "</p>" + product_box(p, "Featured Gadget") + "</p>".join(parts[2:])
            else:
                body_html += product_box(p, "Featured Gadget")
            # second box at the end for conversion
            body_html += product_box(p, "Grab It On Amazon")
        else:
            mentioned = [pp for pp in PRODUCTS if pp["name"].split("(")[0].strip()[:18].lower() in body_html.lower() or pp["category"] in a["keyword"].lower()]
            boxes = mentioned[:4] if mentioned else rnd.sample(PRODUCTS, 3)
            body_html += "<h2>Featured Finds</h2>" + "".join(product_box(x) for x in boxes)

        art_img = p["image"] if p else PRODUCTS[0]["image"]
        jsonld = [
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": a["title"],
                "description": a["meta_description"],
                "image": art_img,
                "datePublished": a["date"],
                "dateModified": a["date"],
                "author": {"@type": "Organization", "name": SITE["name"]},
                "publisher": {"@type": "Organization", "name": SITE["name"]},
                "mainEntityOfPage": canonical,
            },
            {
                "@context": "https://schema.org",
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Home", "item": BASE + "/"},
                    {"@type": "ListItem", "position": 2, "name": "Articles", "item": f"{BASE}/articles/"},
                    {"@type": "ListItem", "position": 3, "name": a["title"], "item": canonical},
                ],
            },
        ]
        faq = extract_faq(a["html"])
        if len(faq) >= 2:
            jsonld.append({
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {"@type": "Question", "name": q,
                     "acceptedAnswer": {"@type": "Answer", "text": ans}}
                    for q, ans in faq
                ],
            })
        cat = p["category"] if p else None
        crumb_cat = f' &rsaquo; <a href="/category/{cat}/">{esc(CATS[cat])}</a>' if cat else ""
        body = f"""<div class="crumbs"><a href="/">Home</a>{crumb_cat} &rsaquo; {esc(a['title'])}</div>
<article><h1>{esc(a['title'])}</h1>
<p class="sub">Published {a['date']} &middot; {SITE['name']}</p>
{DISCLOSURE}
{body_html}</article>{rel_html}"""
        write(f"articles/{a['slug']}/index.html", page(a["title"], a["meta_description"], canonical, body, jsonld, image=art_img))
        urls.append((f"/articles/{a['slug']}/", a["date"]))

    # ---------- product pages ----------
    for p in PRODUCTS:
        canonical = f"{BASE}/products/{p['slug']}/"
        plist = by_product.get(p["asin"], [])
        rel = '<div class="grid">' + "".join(article_card(x) for x in plist[:12]) + "</div>"
        jsonld = [{
            "@context": "https://schema.org",
            "@type": "Product",
            "name": p["full_title"],
            "image": p["image"],
            "description": p["short"],
            "brand": {"@type": "Brand", "name": SITE["name"] + " Pick"},
        }]
        title = f"{p['name']} — Review, Uses & Guides"
        body = f"""<div class="crumbs"><a href="/">Home</a> &rsaquo; <a href="/category/{p['category']}/">{esc(CATS[p['category']])}</a> &rsaquo; {esc(p['name'])}</div>
<h1>{esc(p['name'])}</h1>
{DISCLOSURE}
{product_box(p)}
<p>{esc(p['short'])}</p>
<h2>Guides &amp; Ideas for This Gadget</h2>{rel}"""
        write(f"products/{p['slug']}/index.html", page(title, p["short"], canonical, body, jsonld, image=p["image"]))
        urls.append((f"/products/{p['slug']}/", date.today().isoformat()))

    # ---------- category pages ----------
    for c, cname in CATS.items():
        canonical = f"{BASE}/category/{c}/"
        cprods = [p for p in PRODUCTS if p["category"] == c]
        carts = [a for a in arts if a.get("product") and PRODS[a["product"]]["category"] == c]
        if c == "party":
            carts += roundups
        pcards = "".join(
            f"""<div class="card pcard">{prod_img(p)}<a class="t" href="/products/{p['slug']}/">{esc(p['name'])}</a><p>{esc(p['short'][:100])}&hellip;</p></div>"""
            for p in cprods)
        body = f"""<div class="crumbs"><a href="/">Home</a> &rsaquo; {esc(cname)}</div>
<h1>{esc(cname)}</h1>
<div class="grid">{pcards}</div>
<h2>Latest {esc(cname)} Articles</h2>
<div class="grid">{''.join(article_card(x) for x in carts)}</div>"""
        write(f"category/{c}/index.html", page(f"{cname} — Guides & Gadgets", f"Practical {cname.lower()} guides, ideas and hand-picked kitchen gadgets from {SITE['name']}.", canonical, body))
        urls.append((f"/category/{c}/", date.today().isoformat()))

    # ---------- articles index ----------
    arts_sorted = sorted(arts, key=lambda x: x["date"], reverse=True)
    body = f"""<h1>All Articles</h1><p class="sub">{len(arts)} practical guides on kitchen gadgets, party planning, sourdough baking and more.</p>
<div class="grid">{''.join(article_card(a) for a in arts_sorted)}</div>"""
    write("articles/index.html", page("All Articles — " + SITE["name"], f"Browse all {len(arts)} kitchen gadget guides, party ideas and baking how-tos.", f"{BASE}/articles/", body))
    urls.append(("/articles/", date.today().isoformat()))

    # ---------- home ----------
    latest = arts_sorted[:12]
    pcards = "".join(
        f"""<div class="card pcard">{prod_img(p)}<a class="t" href="/products/{p['slug']}/">{esc(p['name'])}</a><p>{esc(p['short'][:90])}&hellip;</p></div>"""
        for p in PRODUCTS)
    jsonld = [{
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": SITE["name"],
        "url": BASE + "/",
    }]
    body = f"""<div class="hero"><h1>Smart Little Tools for Big Kitchen Fun</h1>
<p>Hands-on guides for the kitchen gadgets, party accessories and baking tools that actually earn their drawer space — from tiki-party drink umbrellas to sourdough spurtles.</p></div>
<h2>Our Favorite Gadgets</h2><div class="grid">{pcards}</div>
<h2>Latest Guides</h2><div class="grid">{''.join(article_card(a) for a in latest)}</div>
<p><a href="/articles/">Browse all {len(arts)} articles &rarr;</a></p>"""
    write("index.html", page(f"{SITE['name']} — Kitchen Gadgets, Party Tools & Baking Guides", "Practical guides and reviews for fun kitchen gadgets: drink umbrellas, sourdough tools, corn holders, gyro pans and more.", BASE + "/", body, jsonld))
    urls.append(("/", date.today().isoformat()))

    # ---------- about / disclosure ----------
    write("about/index.html", page("About — " + SITE["name"], f"About {SITE['name']}: who we are and how we pick the gadgets we cover.", f"{BASE}/about/",
        f"""<h1>About {esc(SITE['name'])}</h1>
<p>{esc(SITE['name'])} is a small US-based publication covering kitchen gadgets, party accessories and baking tools. We focus on inexpensive, fun tools that make cooking and entertaining easier — and we write practical guides on how to actually use them.</p>
<p>Questions? Reach us at nickdavies100@gmail.com.</p>"""))
    urls.append(("/about/", date.today().isoformat()))

    write("affiliate-disclosure/index.html", page("Affiliate Disclosure — " + SITE["name"], "How Kitchen Gadget Grove earns money through Amazon affiliate links.", f"{BASE}/affiliate-disclosure/",
        f"""<h1>Affiliate Disclosure</h1>
<p>{esc(SITE['name'])} is a participant in the Amazon Services LLC Associates Program, an affiliate advertising program designed to provide a means for sites to earn advertising fees by advertising and linking to Amazon.com.</p>
<p>As an Amazon Associate we earn from qualifying purchases. When you click a link to Amazon on this site and make a purchase, we may receive a small commission at no additional cost to you. This helps keep the site running. We only feature products we genuinely think are useful or fun.</p>
<p>Product prices and availability are accurate as of the date/time indicated and are subject to change.</p>"""))
    urls.append(("/affiliate-disclosure/", date.today().isoformat()))

    # ---------- RSS feed ----------
    items = []
    for a in arts_sorted[:50]:
        link = f"{BASE}/articles/{a['slug']}/"
        items.append(
            f"<item><title>{esc(a['title'])}</title><link>{link}</link>"
            f"<guid>{link}</guid><pubDate>{a['date']}</pubDate>"
            f"<description>{esc(a['meta_description'])}</description></item>")
    write("feed.xml",
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{esc(SITE['name'])}</title><link>{BASE}/</link>"
        f"<description>{esc(SITE['tagline'])}</description>"
        + "".join(items) + "</channel></rss>")

    # ---------- IndexNow key ----------
    write(f"{INDEXNOW_KEY}.txt", INDEXNOW_KEY)

    # ---------- sitemap, robots, 404 ----------
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u, d in urls:
        sm.append(f"<url><loc>{BASE}{u}</loc><lastmod>{d}</lastmod></url>")
    sm.append("</urlset>")
    write("sitemap.xml", "\n".join(sm))
    write("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n")
    write("404.html", page("Page Not Found", "That page is missing.", BASE + "/404.html",
        '<h1>Page not found</h1><p>That page wandered off. <a href="/">Head back home</a>.</p>', noindex=True))

    print(f"built {len(urls)} pages -> {PUB}")


if __name__ == "__main__":
    main()
