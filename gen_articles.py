"""Generate SEO articles for Kitchen Gadget Grove via Groq.

Resume-safe: skips articles already in ./articles/. Rotates models on
rate limits / daily caps. Run: python gen_articles.py
"""
import httpx, json, os, re, sys, time, random
from pathlib import Path

ROOT = Path(__file__).parent
ARTICLES = ROOT / "articles"
ARTICLES.mkdir(exist_ok=True)

PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key": os.environ.get("GROQ_API_KEY", ""),
    },
    "cerebras": {
        "url": "https://api.cerebras.ai/v1/chat/completions",
        "key": os.environ.get("CEREBRAS_API_KEY", ""),
    },
}
MODELS = [
    ("cerebras", "gpt-oss-120b"),
    ("cerebras", "zai-glm-4.7"),
    ("groq", "openai/gpt-oss-120b"),
    ("groq", "llama-3.3-70b-versatile"),
    ("cerebras", "gemma-4-31b"),
    ("groq", "qwen/qwen3.6-27b"),
    ("groq", "meta-llama/llama-4-scout-17b-16e-instruct"),
    ("groq", "qwen/qwen3-32b"),
    ("groq", "openai/gpt-oss-20b"),
    ("groq", "llama-3.1-8b-instant"),
]
MODELS = [(pr, m) for pr, m in MODELS if PROVIDERS[pr]["key"]]
if not MODELS:
    sys.exit("no API keys: set GROQ_API_KEY / CEREBRAS_API_KEY")
dead_models = {}  # (provider, model) -> unix time when usable again

CATALOG = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
PRODUCTS = CATALOG["products"]
CATS = CATALOG["categories"]

ARTICLES_PER_PRODUCT = 120
N_ROUNDUPS = 40
TOPIC_BATCH = 30
MAX_MINUTES = float(os.environ.get("MAX_MINUTES", "0"))  # 0 = unlimited
START_TIME = time.time()


def out_of_time():
    return MAX_MINUTES > 0 and (time.time() - START_TIME) > MAX_MINUTES * 60


def pick_model():
    now = time.time()
    for m in MODELS:
        if dead_models.get(m, 0) < now:
            return m
    wake = min(dead_models.values())
    wait = max(5, wake - now)
    if MAX_MINUTES > 0:
        remaining = MAX_MINUTES * 60 - (now - START_TIME)
        if wait > remaining:
            print(f"  all models benched {int(wait)}s > remaining {int(remaining)}s, exiting cleanly", flush=True)
            sys.exit(0)
    print(f"  all models cooling down, sleeping {int(wait)}s", flush=True)
    time.sleep(wait)
    return pick_model()


def strip_think(text):
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def extract_json(text):
    text = strip_think(text)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # first balanced {..} or [..] — try whichever opener appears first
    pairs = [(text.find(o), o, c) for o, c in [("{", "}"), ("[", "]")] if text.find(o) != -1]
    for start, opener, closer in sorted(pairs):
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except Exception:
                        break
    raise ValueError("no parseable JSON in response")


def as_list(data):
    """Normalize model output to a list of topic dicts."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    raise ValueError(f"expected list, got {type(data).__name__}")


def model_extras(provider, model):
    if "gpt-oss" in model:
        return {"reasoning_effort": "low"}
    if provider == "groq" and model.startswith("qwen/"):
        return {"reasoning_format": "hidden"}
    return {}


def call_groq(prompt, max_tokens=4000, want_json=True):
    last_err = None
    for attempt in range(12):
        provider, model = pick_model()
        p = PROVIDERS[provider]
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,
            "max_tokens": max_tokens,
            **model_extras(provider, model),
        }
        try:
            r = httpx.post(p["url"], headers={"Authorization": f"Bearer {p['key']}"}, json=body, timeout=120)
            mk = (provider, model)
            label = f"{provider}:{model}"
            if r.status_code == 400:
                dead_models[mk] = time.time() + 900
                print(f"  {label} 400: {r.text[:150]}", flush=True)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = int(float(r.headers.get("retry-after", "60")))
                txt = r.text[:200]
                # daily/token cap -> bench the model longer
                bench = 3600 if ("per day" in txt or "TPD" in txt or "daily" in txt.lower()) else retry_after
                dead_models[mk] = time.time() + bench
                print(f"  {label} benched {bench}s ({r.status_code})", flush=True)
                continue
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            if want_json:
                return extract_json(content), label
            return strip_think(content), label
        except (httpx.HTTPError, ValueError, KeyError) as e:
            last_err = e
            dead_models[(provider, model)] = time.time() + 20
            print(f"  {provider}:{model} error: {str(e)[:120]}", flush=True)
            time.sleep(2)
    raise RuntimeError(f"all retries failed: {last_err}")


def save(slug, data):
    (ARTICLES / f"{slug}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:80].rstrip("-")


TOPICS_FILE = ROOT / "topics.json"


def topics_call(prompt, want_n):
    """Call until we get a usable list, trying up to 5 times."""
    best = []
    for _ in range(5):
        try:
            arr = as_list(call_groq(prompt, max_tokens=2500)[0])
            if len(arr) > len(best):
                best = arr
            if len(best) >= want_n - 5:
                break
        except (ValueError, RuntimeError) as e:
            print(f"  topics attempt failed: {str(e)[:100]}", flush=True)
    if not best:
        raise RuntimeError("could not get topics")
    return best


def dedup(items):
    seen, out = set(), []
    for t in items:
        if not isinstance(t, dict) or not t.get("title"):
            continue
        s = slugify(t["title"])
        if s and s not in seen:
            seen.add(s)
            out.append(t)
    return out


def gen_topics():
    topics = {"products": {}, "roundups": []}
    if TOPICS_FILE.exists():
        topics = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
        topics.setdefault("products", {})
        topics.setdefault("roundups", [])
    for p in PRODUCTS:
        have = dedup(topics["products"].get(p["asin"], []))
        stall = 0
        while len(have) < ARTICLES_PER_PRODUCT and stall < 6:
            print(f"topics for {p['slug']}: {len(have)}/{ARTICLES_PER_PRODUCT}", flush=True)
            existing = "\n".join(f"- {t['title']}" for t in have[-80:])
            avoid = f"\nALREADY COVERED (do NOT repeat or closely paraphrase any of these):\n{existing}" if have else ""
            prompt = f"""You are an SEO content strategist for a kitchen-gadget blog.
Product: {p['full_title']}
Category: {CATS[p['category']]}

Generate exactly {TOPIC_BATCH} NEW article ideas targeting long-tail Google searches that a buyer of this product might type. Mix: how-to guides, listicles, occasion/party planning, gift guides, comparisons, recipes/use-cases, troubleshooting, buying guides, seasonal/holiday angles, audience angles (kids, seniors, camping, weddings, restaurants), problem-solution angles. Titles natural, specific, 50-65 characters, no year unless 2026.{avoid}

Return ONLY a JSON array of {TOPIC_BATCH} objects: [{{"title": "...", "keyword": "primary search phrase"}}]"""
            try:
                arr = topics_call(prompt, TOPIC_BATCH)
            except RuntimeError as e:
                print(f"  batch failed: {e}", flush=True)
                stall += 1
                continue
            before = len(have)
            have = dedup(have + arr)
            stall = stall + 1 if len(have) == before else 0
            topics["products"][p["asin"]] = have
            TOPICS_FILE.write_text(json.dumps(topics, indent=2, ensure_ascii=False), encoding="utf-8")
        topics["products"][p["asin"]] = have[:ARTICLES_PER_PRODUCT]
    plist = "\n".join(f"- {p['name']}: {p['short']}" for p in PRODUCTS)
    have = dedup(topics.get("roundups", []))
    stall = 0
    while len(have) < N_ROUNDUPS and stall < 4:
        print(f"roundup topics: {len(have)}/{N_ROUNDUPS}", flush=True)
        existing = "\n".join(f"- {t['title']}" for t in have)
        avoid = f"\nALREADY COVERED (do NOT repeat):\n{existing}" if have else ""
        prompt = f"""You are an SEO content strategist for a kitchen-gadget blog. Our catalog:
{plist}

Generate exactly 20 NEW roundup/listicle article ideas that could naturally feature SEVERAL of these products (party planning checklists, summer entertaining, gift guides, sourdough starter kits, BBQ prep, tiki bar setup, etc). Titles 50-65 chars, natural, target real long-tail searches.{avoid}

Return ONLY a JSON array: [{{"title": "...", "keyword": "primary search phrase"}}]"""
        try:
            arr = topics_call(prompt, 20)
        except RuntimeError as e:
            print(f"  batch failed: {e}", flush=True)
            stall += 1
            continue
        before = len(have)
        have = dedup(have + arr)
        stall = stall + 1 if len(have) == before else 0
        topics["roundups"] = have
        TOPICS_FILE.write_text(json.dumps(topics, indent=2, ensure_ascii=False), encoding="utf-8")
    topics["roundups"] = have[:N_ROUNDUPS]
    print("topics saved", flush=True)
    return topics


ARTICLE_PROMPT = """You are a friendly, knowledgeable food & kitchen writer for the blog "Kitchen Gadget Grove". Write an SEO article.

TITLE: {title}
PRIMARY KEYWORD: {keyword}
{product_ctx}

Requirements:
- 750-950 words of genuinely useful, specific, practical content. No fluff, no "in today's fast-paced world" openers. Write like a real person who uses this stuff.
- Structure with <h2> sections (4-6 of them) and short paragraphs. Use one <ul> or <ol> list where natural. End with a short FAQ section: <h2>FAQ</h2> then 3 <h3> questions with answers.
- Weave the primary keyword naturally into the first 100 words and a couple of h2s.
- Where the product is relevant, mention it naturally by name (no link needed - the site inserts the product box).
- Do NOT invent fake statistics, fake reviews, or prices.
- American English, US audience.

Return ONLY JSON: {{"meta_description": "compelling 140-155 char description", "html": "<p>...article body html, no h1, no doctype...</p>"}}"""


def product_context(p):
    return f"FEATURED PRODUCT (this article promotes it): {p['full_title']}. Sold as: {p['name']}. {p['short']}"


def roundup_context():
    lines = "\n".join(f"- {p['name']}: {p['short']}" for p in PRODUCTS)
    return "FEATURED PRODUCTS (mention the ones that fit this topic naturally by name):\n" + lines


def main():
    topics = gen_topics()
    jobs = []
    for p in PRODUCTS:
        for t in topics["products"][p["asin"]]:
            jobs.append({"topic": t, "product": p["asin"], "type": "product"})
    for t in topics["roundups"]:
        jobs.append({"topic": t, "product": None, "type": "roundup"})

    random.shuffle(jobs)
    done = 0
    prods = {p["asin"]: p for p in PRODUCTS}
    for job in jobs:
        if out_of_time():
            print(f"time cap {MAX_MINUTES}min reached, exiting cleanly ({done}/{len(jobs)})", flush=True)
            break
        t = job["topic"]
        slug = slugify(t["title"])
        if (ARTICLES / f"{slug}.json").exists():
            done += 1
            continue
        p = prods.get(job["product"])
        ctx = product_context(p) if p else roundup_context()
        prompt = ARTICLE_PROMPT.format(title=t["title"], keyword=t.get("keyword", ""), product_ctx=ctx)
        try:
            data, model = call_groq(prompt, max_tokens=3500)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                data = data[0]
            html_body = data.get("html", "")
            if len(re.sub(r"<[^>]+>", "", html_body).split()) < 300:
                print(f"  SHORT, retrying once: {slug}", flush=True)
                data, model = call_groq(prompt, max_tokens=3500)
                html_body = data.get("html", "")
            art = {
                "slug": slug,
                "title": t["title"],
                "keyword": t.get("keyword", ""),
                "meta_description": data.get("meta_description", "")[:160],
                "html": html_body,
                "type": job["type"],
                "product": job["product"],
                "model": model,
            }
            save(slug, art)
            done += 1
            print(f"[{done}/{len(jobs)}] {slug} ({model})", flush=True)
        except Exception as e:
            print(f"FAILED {slug}: {str(e)[:150]}", flush=True)
        time.sleep(1.5)
    print(f"DONE: {done}/{len(jobs)} articles in {ARTICLES}", flush=True)


if __name__ == "__main__":
    main()
