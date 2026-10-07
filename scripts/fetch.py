"""Pull health x tech x business news into data/items.json.

Runs in GitHub Actions on a schedule (see .github/workflows/update.yml).
Every source is fetched independently; a broken feed is logged in the
output file and shown on the dashboard instead of failing the run.
"""
import calendar
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import feedparser
import requests

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.json"
OUT = ROOT / "data" / "items.json"
UA = "Mozilla/5.0 (compatible; HealthTechDesk/1.0; +https://github.com)"
NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------- keywords
TECH = r"\b(ai|a\.i\.|artificial intelligence|machine learning|deep learning|llms?|large language model|generative|gpt|chatbot|algorithm|software|digital|app|apps|platform|startup|start-up|telehealth|telemedicine|virtual care|wearable|robot\w*|automation|cloud|ehr|electronic health record|epic systems|interoperab\w*|real-world data|health data|analytics|remote (patient )?monitoring|saas|cyber\w*|computational|genomic\w*|precision medicine)\b"
BUSINESS = r"\b(raises?|raised|funding|series [a-f]|seed round|venture|vc|investors?|investment|acquir\w*|acquisition|merger|m&a|ipo|spac|valuation|revenue|earnings|profit\w*|margin|layoffs?|cost\w*|price|pricing|reimburse\w*|payers?|insurers?|medicare advantage|medicare|medicaid|cms|market access|value-based|deal|partnership|antitrust|economic\w*|spending|affordab\w*)\b"
HEALTH = r"\b(health\w*|medical|medicine|clinical|clinician\w*|hospital\w*|patients?|pharma\w*|biotech\w*|drugs?|fda|medicare|medicaid|payers?|insurers?|dental|dentist\w*|medtech|therapeutics?|diagnostic\w*|care)\b"
DEALS = r"\b(raises?|raised|funding round|series [a-f]|seed round|closes? \$|secures? \$|lands? \$|acquir\w*|acquisition|merger|merges|ipo|goes public|spac|valuation)\b"
REG = r"\b(fda|510\(k\)|de novo|clearance|clears|cleared|approv\w*|cms|hhs|onc|proposed rule|final rule|guidance|reimbursement code|cpt code|ftc|doj|congress|senate|bill)\b"
HOT = r"\b(ai|llms?|openai|anthropic|google|microsoft|amazon|apple|nvidia|epic|glp-1|ozempic|wegovy|medicare advantage|unitedhealth|optum|layoffs?|first-ever|first)\b"
DENTAL = r"\b(dental|dentist\w*|oral health|orthodont\w*|caries|periodont\w*)\b"

STOP = set("a an the of to in for on and or with by at from as is are be its it this that new how why what into over after amid via vs says said will".split())


def rx(p):
    return re.compile(p, re.I)


TECH_RX, BIZ_RX, HEALTH_RX, DEALS_RX, REG_RX, HOT_RX, DENTAL_RX = map(rx, [TECH, BUSINESS, HEALTH, DEALS, REG, HOT, DENTAL])
MONEY_RX = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?(m|mm|million|b|bn|billion)\b", re.I)


def clean(text, limit=320):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def to_iso(struct):
    if not struct:
        return None
    return datetime.fromtimestamp(calendar.timegm(struct), timezone.utc).isoformat()


def item_id(url, title):
    key = (url or "").split("?utm")[0] or title
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def money_millions(text):
    best = 0.0
    for num, unit in MONEY_RX.findall(text or ""):
        v = float(num) * (1000 if unit.lower().startswith("b") else 1)
        best = max(best, v)
    return best


def classify(default_lane, text):
    if default_lane == "takes":  # newsletters and VC takes stay in their own lane
        return default_lane
    if DEALS_RX.search(text) and money_millions(text) or re.search(r"\b(acquir\w*|acquisition|merger|ipo)\b", text, re.I):
        return "deals"
    if default_lane in ("industry", "takes") and REG_RX.search(text) and re.search(r"\b(fda|cms|510\(k\)|de novo)\b", text, re.I):
        return "regulatory"
    return default_lane


def keep(filter_name, text):
    if filter_name == "intersect":
        return bool(TECH_RX.search(text) or BIZ_RX.search(text))
    if filter_name == "health":
        return bool(HEALTH_RX.search(text))
    return True


# ---------------------------------------------------------------- fetchers
def get(url):
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=25)
    r.raise_for_status()
    return r.content


def from_feed(name, url, lane, filt, tier=0, gnews=False):
    parsed = feedparser.parse(get(url))
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"not a feed ({parsed.bozo_exception.__class__.__name__})")
    out = []
    for e in parsed.entries[:80]:
        title = clean(e.get("title"), 240)
        source = name
        if gnews:
            src = e.get("source") or {}
            source = src.get("title") or name
            title = re.sub(r"\s+-\s+[^-]+$", "", title)  # drop " - Outlet"
        summary = "" if gnews else clean(e.get("summary") or e.get("description"))
        text = f"{title} {summary}"
        if not title or not keep(filt, text):
            continue
        published = to_iso(e.get("published_parsed") or e.get("updated_parsed"))
        out.append({
            "id": item_id(e.get("link"), title),
            "title": title,
            "summary": summary,
            "url": e.get("link"),
            "source": source,
            "lane": classify(lane, text),
            "published": published,
            "tier": tier,
        })
    return out


class _LinkGrabber(HTMLParser):
    """Collects <a> links whose href contains a pattern, with the text chunks inside them."""

    def __init__(self, pattern):
        super().__init__()
        self.pattern, self.links, self.cur = pattern, [], None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if self.pattern in href:
                self.cur = {"href": href, "chunks": []}

    def handle_endtag(self, tag):
        if tag == "a" and self.cur:
            self.links.append(self.cur)
            self.cur = None

    def handle_data(self, data):
        if self.cur is not None and data.strip():
            self.cur["chunks"].append(data.strip())


DATE_RX = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}),? (\d{4})\b")


def from_page(name, url, pattern, lane, limit=10, tier=0):
    """For newsletters without RSS (e.g. beehiiv sites with RSS turned off): read the archive page."""
    g = _LinkGrabber(pattern)
    g.feed(get(url).decode("utf-8", "replace"))
    out, seen = [], set()
    for link in g.links:
        href = urljoin(url, link["href"]).split("?")[0]
        if href in seen:
            continue
        chunks = [c for c in link["chunks"] if not DATE_RX.fullmatch(c)]
        title = next((c for c in chunks if 15 <= len(c) <= 220), "")
        if not title:
            continue
        seen.add(href)
        joined = " ".join(link["chunks"])
        m = DATE_RX.search(joined)
        published = None
        if m:
            try:
                published = datetime.strptime(f"{m.group(1)[:3]} {m.group(2)} {m.group(3)}", "%b %d %Y").replace(hour=12, tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
        rest = [c for c in chunks if c != title and len(c) > 25]
        out.append({
            "id": item_id(href, title),
            "title": clean(title, 240),
            "summary": clean(rest[0]) if rest else "",
            "url": href,
            "source": name,
            "lane": lane,
            "published": published,
            "undated": published is None,
            "tier": tier,
        })
        if len(out) >= limit:
            break
    if not out:
        raise ValueError(f"no links containing '{pattern}' found; the page layout may have changed")
    return out


def from_openfda(cfg):
    start = (NOW - timedelta(days=cfg.get("days", 45))).strftime("%Y%m%d")
    end = NOW.strftime("%Y%m%d")
    url = f"https://api.fda.gov/device/510k.json?search=decision_date:[{start}+TO+{end}]&sort=decision_date:desc&limit=1000"
    data = json.loads(get(url))
    soft = rx(r"\b(software|ai|artificial intelligence|algorithm|machine learning|deep learning|neural|automated|computer[- ]aided|cad[ex]?|image analysis|analysis software|triage|detection|notification)\b")
    out = []
    for r in data.get("results", []):
        name = r.get("device_name", "")
        dental = r.get("advisory_committee") == "DE"
        if not (soft.search(name) or (dental and cfg.get("keep") == "ai_software_dental")):
            continue
        k = r.get("k_number", "")
        d = r.get("decision_date", "")
        title = f"510(k) cleared: {name.title()} ({r.get('applicant', '').strip()})"
        out.append({
            "id": item_id(k, title),
            "title": clean(title, 240),
            "summary": f"{r.get('advisory_committee_description', '')} panel. {k}, decision {d}. "
                       f"{'Dental device. ' if dental else ''}Official database entries usually appear weeks after the decision.",
            "url": f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={k}",
            "source": "FDA 510(k) database",
            "lane": "regulatory",
            "published": f"{d}T12:00:00+00:00" if len(d) == 10 else None,
            "tier": 0,
        })
    return out


# ---------------------------------------------------------------- scoring
def tokens(title):
    return {w for w in re.findall(r"[a-z0-9$]+", title.lower()) if w not in STOP and len(w) > 2}


def cluster(items):
    """Group the same story across outlets; coverage = number of outlets."""
    items.sort(key=lambda i: i.get("published") or "")
    clusters = []
    for it in items:
        t = tokens(it["title"])
        when = parse_dt(it.get("published"))
        home = None
        for c in clusters:
            if when and c["when"] and abs((when - c["when"]).total_seconds()) > 72 * 3600:
                continue
            inter = len(t & c["tokens"])
            if t and inter / max(1, min(len(t), len(c["tokens"]))) >= 0.6 and inter >= 3:
                home = c
                break
        if home is None:
            home = {"id": it["id"], "tokens": t, "when": when, "sources": set()}
            clusters.append(home)
        home["sources"].add(it["source"])
        it["cluster"] = home["id"]
    sizes = {c["id"]: len(c["sources"]) for c in clusters}
    for it in items:
        it["coverage"] = sizes[it["cluster"]]


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def heat(it):
    text = f"{it['title']} {it.get('summary', '')}"
    score = 1
    cov = it.get("coverage", 1)
    score += 2 if cov >= 4 else 1 if cov >= 2 else 0
    m = money_millions(text)
    score += 2 if m >= 250 else 1 if m >= 50 else 0
    score += 1 if HOT_RX.search(text) else 0
    score += 1 if it.get("tier") else 0
    score += 1 if DENTAL_RX.search(text) else 0
    pub = parse_dt(it.get("published"))
    if pub and NOW - pub > timedelta(days=3):
        score -= 1
    return max(1, min(5, score))


# ---------------------------------------------------------------- optional angles
def add_angles(items):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return
    voice = os.environ.get("ANGLE_PERSPECTIVE", "a dentist with an MSPH in epidemiology who builds dental AI and works in life sciences consulting")
    model = os.environ.get("ANGLE_MODEL", "claude-sonnet-5-5")
    todo = [i for i in items if not i.get("angle") and i["heat"] >= 4 and is_recent(i, 48)][:10]
    for it in todo:
        prompt = (
            f"News item ({it['lane']}): {it['title']}\nSource: {it['source']}\n{it.get('summary', '')}\n\n"
            f"In one or two plain sentences, give a sharp, specific take for a LinkedIn reaction from {voice}. "
            "Point at what most people posting about this will miss. No hype words, no em dashes. Return only the take."
        )
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": 160, "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            r.raise_for_status()
            it["angle"] = "".join(b.get("text", "") for b in r.json().get("content", [])).strip()
        except Exception as e:  # angles are a bonus; never fail the run
            print(f"angle failed for {it['id']}: {e}", file=sys.stderr)
            break


def is_recent(it, hours):
    p = parse_dt(it.get("published"))
    return bool(p and NOW - p < timedelta(hours=hours))


# ---------------------------------------------------------------- main
def main():
    cfg = json.loads(SOURCES.read_text())
    previous = {}
    if OUT.exists():
        try:
            previous = {i["id"]: i for i in json.loads(OUT.read_text()).get("items", [])}
        except (ValueError, KeyError):
            previous = {}

    fresh, report = [], []

    def run(name, fn):
        t0 = time.time()
        try:
            got = fn()
            fresh.extend(got)
            report.append({"name": name, "ok": True, "count": len(got)})
        except Exception as e:
            report.append({"name": name, "ok": False, "count": 0, "error": f"{e.__class__.__name__}: {str(e)[:140]}"})
        print(f"{name}: {report[-1]} ({time.time() - t0:.1f}s)")

    for f in cfg.get("feeds", []):
        run(f["name"], lambda f=f: from_feed(f["name"], f["url"], f["lane"], f.get("filter"), f.get("tier", 0)))
    for pg in cfg.get("pages", []):
        run(pg["name"], lambda pg=pg: from_page(pg["name"], pg["url"], pg.get("link_contains", "/p/"), pg.get("lane", "takes"), pg.get("limit", 10), pg.get("tier", 0)))
    window = cfg.get("google_news_window", "1d")
    for g in cfg.get("google_news", []):
        url = f"https://news.google.com/rss/search?q={quote_plus(g['query'] + ' when:' + window)}&hl=en-US&gl=US&ceid=US:en"
        run(f"Google News: {g['query'][:48]}…", lambda g=g, url=url: from_feed("Google News", url, g["lane"], None, 0, gnews=True))
    if cfg.get("openfda_510k", {}).get("enabled"):
        run("FDA 510(k) database", lambda: from_openfda(cfg["openfda_510k"]))

    merged = dict(previous)
    for it in fresh:
        old = merged.get(it["id"])
        it["first_seen"] = (old or {}).get("first_seen") or NOW.isoformat()
        if old and old.get("angle"):
            it["angle"] = old["angle"]
        if not it.get("published") and not it.get("undated"):
            it["published"] = it["first_seen"]
        merged[it["id"]] = it

    cutoff = NOW - timedelta(days=cfg.get("keep_days", 21))
    # retention runs on first_seen so slow sources (the 510(k) database lags weeks) still show up
    items = [i for i in merged.values() if (parse_dt(i.get("first_seen")) or NOW) >= cutoff]
    items = [i for i in items if (parse_dt(i.get("published")) or NOW) <= NOW + timedelta(hours=6)]

    cluster(items)
    for it in items:
        it["heat"] = heat(it)
        it["dental"] = bool(DENTAL_RX.search(f"{it['title']} {it.get('summary', '')}"))
    add_angles(items)
    items.sort(key=lambda i: i.get("published") or "", reverse=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": NOW.isoformat(),
        "sources": report,
        "items": items,
    }, indent=1, ensure_ascii=False))
    ok = sum(1 for r in report if r["ok"])
    print(f"\n{len(items)} items kept, {len(fresh)} fetched this run, {ok}/{len(report)} sources OK")
    if ok == 0:
        sys.exit("Every source failed; leaving the run red so you notice.")


if __name__ == "__main__":
    main()
