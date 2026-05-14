#!/usr/bin/env python3
"""
Australian Restaurant Discount Scanner
Scans EatClub, Groupon AU, Scoopon, and the web for restaurant discounts.
Run: python restaurant_discounts.py
Then open http://localhost:8765 in your browser.
"""

import concurrent.futures
import json
import re
import sys
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

missing = []
try:
    import requests
except ImportError:
    missing.append("requests")
try:
    from bs4 import BeautifulSoup
except ImportError:
    missing.append("beautifulsoup4")

if missing:
    print(f"Missing packages: {', '.join(missing)}")
    print(f"Install with: pip install {' '.join(missing)}")
    sys.exit(1)


PORT = 8765


@dataclass
class Deal:
    title: str
    source: str
    url: str
    discount: str = ""
    cuisine: str = ""
    location: str = ""
    description: str = ""


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "DNT": "1",
}

SOURCE_DOMAINS = {
    "eatclub.com.au": "EatClub",
    "groupon.com.au": "Groupon",
    "scoopon.com.au": "Scoopon",
    "lasoo.com.au": "Lasoo",
    "quandoo.com.au": "Quandoo",
    "dimmi.com.au": "Dimmi",
    "lunchbox.com.au": "Lunchbox",
}

AU_CITIES = [
    "Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide",
    "Canberra", "Hobart", "Darwin", "Gold Coast", "Newcastle",
    "Wollongong", "Geelong", "Townsville", "Cairns", "Toowoomba",
    "Ballarat", "Bendigo", "Launceston", "Mackay", "Rockhampton",
]

CUISINES = [
    "Italian", "Chinese", "Japanese", "Thai", "Indian",
    "Mexican", "Greek", "Lebanese", "Vietnamese", "Korean",
    "French", "American", "Australian", "Spanish", "Turkish",
    "Pizza", "Sushi", "Burger", "Seafood", "Steakhouse",
]


def _extract_source(url: str) -> str:
    for domain, name in SOURCE_DOMAINS.items():
        if domain in url:
            return name
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if m:
        return m.group(1).split(".")[0].title()
    return "Web"


def _extract_discount(text: str) -> str:
    patterns = [
        r"(\d+%\s*off)",
        r"(save\s+\$[\d,]+)",
        r"(\$\d+\s+(?:deal|voucher|value))",
        r"(half[\s\-]*price)",
        r"(buy\s+one\s+get\s+one|bogo)",
        r"(\d+\s+for\s+\$\d+)",
        r"(free\s+\w+(?:\s+\w+)?)",
        r"(happy\s+hour)",
        r"(\d+%\s*discount)",
        r"(two[\s\-]for[\s\-]one)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def scan_ddg(city: str, cuisine: Optional[str] = None) -> list:
    """Search DuckDuckGo HTML for restaurant deals."""
    deals = []
    seen: set = set()

    site_filter = " OR ".join(f"site:{d}" for d in SOURCE_DOMAINS)
    cuisine_part = f" {cuisine}" if cuisine else ""

    queries = [
        f'restaurant{cuisine_part} discount deal {city} Australia ({site_filter})',
        f'"{city}" restaurant "% off" OR "save" OR "deal" Australia -site:reddit.com',
    ]

    for query in queries:
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            for result in soup.find_all("div", class_="result")[:12]:
                title_el = result.find("a", class_="result__a")
                snippet_el = result.find("a", class_="result__snippet")

                if not title_el:
                    continue

                href = title_el.get("href", "")
                real_url = href
                if "uddg=" in href:
                    m = re.search(r"uddg=([^&]+)", href)
                    if m:
                        real_url = unquote(m.group(1))

                if real_url in seen:
                    continue
                seen.add(real_url)

                title = title_el.get_text(strip=True)
                desc = snippet_el.get_text(strip=True) if snippet_el else ""

                deals.append(
                    Deal(
                        title=title,
                        source=_extract_source(real_url),
                        url=real_url,
                        discount=_extract_discount(title + " " + desc),
                        cuisine=cuisine or "",
                        location=city,
                        description=desc[:200],
                    )
                )
        except Exception:
            pass

        time.sleep(0.4)

    return deals


def scan_eatclub(city: str, cuisine: Optional[str] = None) -> list:
    """Scrape EatClub for restaurant deals."""
    deals = []
    url = f"https://www.eatclub.com.au/restaurants?location={quote_plus(city)}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        script = soup.find("script", id="__NEXT_DATA__")
        if script and script.string:
            data = json.loads(script.string)
            page_props = data.get("props", {}).get("pageProps", {})
            restaurants = (
                page_props.get("restaurants")
                or page_props.get("initialData", {}).get("restaurants")
                or []
            )
            for r in restaurants[:25]:
                name = r.get("name") or r.get("restaurantName", "")
                if not name:
                    continue
                if cuisine and cuisine.lower() not in str(r).lower():
                    continue
                slug = r.get("slug") or r.get("restaurantSlug", "")
                deal_info = r.get("deal") or {}
                discount_str = (
                    deal_info.get("description")
                    or deal_info.get("shortDescription")
                    or _extract_discount(str(deal_info))
                )
                cuisines = r.get("cuisineTypes") or []
                cuisine_str = cuisines[0] if cuisines else r.get("cuisine", "")
                deals.append(
                    Deal(
                        title=name,
                        source="EatClub",
                        url=(
                            f"https://www.eatclub.com.au/restaurant/{slug}"
                            if slug
                            else "https://www.eatclub.com.au"
                        ),
                        discount=discount_str,
                        cuisine=cuisine_str,
                        location=city,
                        description=r.get("description", "")[:200],
                    )
                )
    except Exception:
        pass

    return deals


def scan_groupon(city: str, cuisine: Optional[str] = None) -> list:
    """Scrape Groupon AU for restaurant deals."""
    deals = []
    city_slug = city.lower().replace(" ", "-")
    url = f"https://www.groupon.com.au/local/au/{city_slug}/restaurants"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for card in soup.find_all(
            ["article", "div"], attrs={"data-bhw": True}
        )[:15]:
            name = card.get("data-bhw-title", "")
            if not name:
                h = card.find(["h3", "h2", "h4"])
                name = h.get_text(strip=True) if h else ""
            if not name:
                continue
            if cuisine and cuisine.lower() not in str(card).lower():
                continue

            discount_el = card.find(
                class_=re.compile(r"discount|savings|off", re.I)
            )
            discount = (
                discount_el.get_text(strip=True)
                if discount_el
                else _extract_discount(str(card))
            )

            link = card.find("a", href=True)
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"https://www.groupon.com.au{href}"

            deals.append(
                Deal(
                    title=name[:80],
                    source="Groupon",
                    url=href or url,
                    discount=discount,
                    cuisine=cuisine or "",
                    location=city,
                )
            )
    except Exception:
        pass

    return deals


def scan_scoopon(city: str, cuisine: Optional[str] = None) -> list:
    """Scrape Scoopon for restaurant deals."""
    deals = []
    cuisine_part = f" {cuisine}" if cuisine else " restaurant"
    url = (
        f"https://www.scoopon.com.au/experiences/?"
        f"q={quote_plus(cuisine_part.strip())}&location={quote_plus(city)}"
    )

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for card in soup.find_all(
            class_=re.compile(r"deal|product|card", re.I)
        )[:15]:
            title_el = card.find(["h2", "h3", "h4"])
            if not title_el:
                continue
            name = title_el.get_text(strip=True)
            if not name or len(name) < 3:
                continue

            link = card.find("a", href=True)
            href = link["href"] if link else ""
            if href and not href.startswith("http"):
                href = f"https://www.scoopon.com.au{href}"

            price_el = card.find(class_=re.compile(r"price|discount|save", re.I))
            discount = (
                price_el.get_text(strip=True)
                if price_el
                else _extract_discount(str(card))
            )

            deals.append(
                Deal(
                    title=name[:80],
                    source="Scoopon",
                    url=href or url,
                    discount=discount,
                    cuisine=cuisine or "",
                    location=city,
                )
            )
    except Exception:
        pass

    return deals


def scan_all(city: str, cuisine: Optional[str] = None) -> list:
    """Scan all sources concurrently and deduplicate."""
    all_deals: list = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(scan_eatclub, city, cuisine): "EatClub",
            ex.submit(scan_groupon, city, cuisine): "Groupon",
            ex.submit(scan_scoopon, city, cuisine): "Scoopon",
            ex.submit(scan_ddg, city, cuisine): "Web",
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                all_deals.extend(future.result())
            except Exception:
                pass

    seen: set = set()
    unique = []
    for d in all_deals:
        key = re.sub(r"\W+", "", d.title.lower())[:24]
        if key not in seen:
            seen.add(key)
            unique.append(d)

    return unique[:30]


# ── Embedded HTML ──────────────────────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AU Restaurant Discounts</title>
<style>
:root {
  --bg: #0f1923;
  --surface: #1c2a38;
  --surface2: #243547;
  --accent: #f5a623;
  --danger: #e74c3c;
  --green: #27ae60;
  --text: #ecf0f1;
  --muted: #7f8c8d;
  --border: #2c3e50;
  --radius: 12px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh;line-height:1.5}
a{color:inherit;text-decoration:none}

/* ── Header ── */
header{
  background:linear-gradient(135deg,#0d1b2a 0%,#1a2f45 100%);
  border-bottom:1px solid var(--border);
  padding:28px 20px 24px;
  text-align:center;
}
.logo{font-size:2rem;font-weight:900;color:var(--accent);letter-spacing:3px;text-transform:uppercase}
.logo span{color:#fff}
.tagline{color:var(--muted);margin-top:6px;font-size:0.9rem;letter-spacing:1px}
.flag{font-size:1.5rem;margin-right:6px}

/* ── Search card ── */
.wrap{max-width:820px;margin:0 auto;padding:0 16px}
.search-card{
  background:var(--surface);border-radius:var(--radius);
  padding:28px;border:1px solid var(--border);
  margin:28px auto;
}
.form-row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.fg{flex:1;min-width:150px}
label{display:block;font-size:11px;color:var(--muted);margin-bottom:6px;letter-spacing:1.5px;text-transform:uppercase;font-weight:600}
select,input[type=text]{
  width:100%;padding:11px 14px;
  background:#0f1923;border:1px solid var(--border);
  border-radius:8px;color:var(--text);font-size:14px;outline:none;
  transition:border-color .2s;
}
select:focus,input:focus{border-color:var(--accent)}
option{background:#1c2a38}

.btn{
  padding:11px 24px;background:var(--accent);color:#000;
  font-weight:700;border:none;border-radius:8px;cursor:pointer;
  font-size:13px;letter-spacing:1.5px;text-transform:uppercase;
  transition:opacity .15s,transform .1s;white-space:nowrap;min-width:130px;
  display:inline-flex;align-items:center;gap:6px;justify-content:center;
}
.btn:hover{opacity:.88}
.btn:active{transform:scale(.97)}
.btn:disabled{opacity:.45;cursor:not-allowed;transform:none}

/* quick links */
.quick-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px;align-items:center}
.ql-label{font-size:11px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}
.chip{
  padding:6px 14px;background:var(--surface2);border:1px solid var(--border);
  border-radius:20px;font-size:12px;color:var(--muted);cursor:pointer;
  transition:all .2s;
}
.chip:hover{border-color:var(--accent);color:var(--accent)}

/* ── Results ── */
.results-bar{
  display:flex;justify-content:space-between;align-items:center;
  margin-bottom:14px;
}
.results-title{font-weight:600;font-size:1rem}
.count-badge{
  padding:3px 12px;background:rgba(245,166,35,.15);
  color:var(--accent);border-radius:20px;font-size:12px;font-weight:700;
  border:1px solid rgba(245,166,35,.3);
}

.deals-grid{display:grid;gap:10px}

.deal-card{
  background:var(--surface);border-radius:var(--radius);
  padding:16px 18px;border:1px solid var(--border);
  display:flex;align-items:center;gap:14px;
  transition:border-color .2s,transform .1s;
  cursor:pointer;
}
.deal-card:hover{border-color:var(--accent);transform:translateY(-1px)}

.source-badge{
  padding:4px 10px;border-radius:10px;font-size:10px;font-weight:700;
  letter-spacing:1px;text-transform:uppercase;white-space:nowrap;flex-shrink:0;
  background:rgba(245,166,35,.12);color:var(--accent);
  border:1px solid rgba(245,166,35,.25);
}
.src-EatClub{background:rgba(0,200,100,.12);color:#00c864;border-color:rgba(0,200,100,.25)}
.src-Groupon{background:rgba(0,160,80,.12);color:#00a050;border-color:rgba(0,160,80,.25)}
.src-Scoopon{background:rgba(255,60,0,.12);color:#ff6040;border-color:rgba(255,60,0,.25)}
.src-Lasoo{background:rgba(0,160,220,.12);color:#00b0dd;border-color:rgba(0,160,220,.25)}
.src-Quandoo{background:rgba(180,0,220,.12);color:#c044ee;border-color:rgba(180,0,220,.25)}

.deal-body{flex:1;min-width:0}
.deal-title{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.95rem}
.deal-desc{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:3px}

.discount-pill{
  padding:5px 12px;
  background:rgba(231,76,60,.15);color:var(--danger);
  border:1px solid rgba(231,76,60,.3);border-radius:8px;
  font-size:11px;font-weight:700;white-space:nowrap;flex-shrink:0;
  text-transform:uppercase;letter-spacing:.5px;
}

/* ── States ── */
.state{text-align:center;padding:52px 20px;color:var(--muted)}
.state-icon{font-size:52px;margin-bottom:14px}
.state-title{font-size:1.1rem;font-weight:600;color:var(--text);margin-bottom:6px}
.state-sub{font-size:.9rem}

.spinner{
  width:40px;height:40px;
  border:3px solid var(--border);border-top-color:var(--accent);
  border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 18px;
}
@keyframes spin{to{transform:rotate(360deg)}}

.error-box{
  background:rgba(231,76,60,.1);border:1px solid rgba(231,76,60,.3);
  border-radius:var(--radius);padding:20px;color:var(--danger);text-align:center;
}

/* ── Filters ── */
.filter-row{
  display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center;
}
.filter-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.filter-chip{
  padding:5px 12px;border-radius:20px;font-size:11px;border:1px solid var(--border);
  cursor:pointer;transition:all .15s;background:transparent;color:var(--muted);
}
.filter-chip.active,.filter-chip:hover{
  background:rgba(245,166,35,.12);color:var(--accent);border-color:rgba(245,166,35,.4);
}

/* ── Footer ── */
footer{
  border-top:1px solid var(--border);padding:20px;text-align:center;
  color:var(--muted);font-size:12px;margin-top:32px;
}
footer a{color:var(--accent)}

@media(max-width:540px){
  .logo{font-size:1.4rem;letter-spacing:1px}
  .form-row{flex-direction:column}
  .btn{width:100%}
  .deal-card{flex-wrap:wrap}
}
</style>
</head>
<body>

<header>
  <div class="logo"><span class="flag">🇦🇺</span> AU Restaurant <span>Discounts</span></div>
  <div class="tagline">Scan for the best deals across Australia &mdash; powered by EatClub, Groupon &amp; more</div>
</header>

<div class="wrap">

  <!-- Search -->
  <div class="search-card">
    <div class="form-row">
      <div class="fg">
        <label>City</label>
        <select id="city">
          <option value="Sydney">Sydney</option>
          <option value="Melbourne">Melbourne</option>
          <option value="Brisbane">Brisbane</option>
          <option value="Perth">Perth</option>
          <option value="Adelaide">Adelaide</option>
          <option value="Canberra">Canberra</option>
          <option value="Hobart">Hobart</option>
          <option value="Darwin">Darwin</option>
          <option value="Gold Coast">Gold Coast</option>
          <option value="Newcastle">Newcastle</option>
          <option value="Wollongong">Wollongong</option>
          <option value="Geelong">Geelong</option>
          <option value="Townsville">Townsville</option>
          <option value="Cairns">Cairns</option>
          <option value="Toowoomba">Toowoomba</option>
        </select>
      </div>
      <div class="fg">
        <label>Cuisine (optional)</label>
        <input type="text" id="cuisine" placeholder="e.g. Italian, Japanese, Pizza…" list="cuisine-list" autocomplete="off">
        <datalist id="cuisine-list">
          <option value="Italian"><option value="Chinese"><option value="Japanese">
          <option value="Thai"><option value="Indian"><option value="Mexican">
          <option value="Greek"><option value="Vietnamese"><option value="Korean">
          <option value="Pizza"><option value="Sushi"><option value="Burger">
          <option value="Seafood"><option value="Steakhouse"><option value="French">
        </datalist>
      </div>
      <button class="btn" id="scan-btn" onclick="doScan()">
        <span id="btn-icon">🔍</span> <span id="btn-text">Scan Deals</span>
      </button>
    </div>

    <div class="quick-links">
      <span class="ql-label">Direct links:</span>
      <a href="https://www.eatclub.com.au/restaurants" target="_blank" class="chip">EatClub</a>
      <a href="https://www.groupon.com.au/local/au/sydney/restaurants" target="_blank" class="chip">Groupon AU</a>
      <a href="https://www.scoopon.com.au" target="_blank" class="chip">Scoopon</a>
      <a href="https://www.lasoo.com.au" target="_blank" class="chip">Lasoo</a>
      <a href="https://www.quandoo.com.au" target="_blank" class="chip">Quandoo</a>
      <a href="https://www.dimmi.com.au" target="_blank" class="chip">Dimmi</a>
    </div>
  </div>

  <!-- Results -->
  <div id="results">
    <div class="state">
      <div class="state-icon">🦘</div>
      <div class="state-title">Ready to scan</div>
      <div class="state-sub">Choose a city and click <strong>Scan Deals</strong> to find restaurant discounts</div>
    </div>
  </div>

</div>

<footer>
  Australian Restaurant Discount Scanner &bull;
  Data sourced from <a href="https://www.eatclub.com.au" target="_blank">EatClub</a>,
  <a href="https://www.groupon.com.au" target="_blank">Groupon</a>,
  <a href="https://www.scoopon.com.au" target="_blank">Scoopon</a> &amp; web search
</footer>

<script>
'use strict';

let allDeals = [];
let activeSource = 'All';

function esc(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function doScan() {
  const city = document.getElementById('city').value;
  const cuisine = document.getElementById('cuisine').value.trim();
  const btn = document.getElementById('scan-btn');
  const icon = document.getElementById('btn-icon');
  const txt = document.getElementById('btn-text');

  btn.disabled = true;
  icon.textContent = '⏳';
  txt.textContent = 'Scanning…';

  document.getElementById('results').innerHTML = `
    <div class="state">
      <div class="spinner"></div>
      <div class="state-title">Scanning for deals in ${esc(city)}…</div>
      <div class="state-sub">Checking EatClub, Groupon, Scoopon and the web</div>
    </div>
  `;

  try {
    const params = new URLSearchParams({ city });
    if (cuisine) params.append('cuisine', cuisine);

    const resp = await fetch('/api/search?' + params);
    if (!resp.ok) throw new Error('Server returned ' + resp.status);

    const data = await resp.json();
    allDeals = data.deals || [];
    activeSource = 'All';
    renderResults(data.city, data.cuisine);
  } catch (e) {
    document.getElementById('results').innerHTML = `
      <div class="error-box">
        ⚠ Could not fetch results.<br>
        <small style="opacity:.7">${esc(e.message)}</small>
      </div>
    `;
  } finally {
    btn.disabled = false;
    icon.textContent = '🔍';
    txt.textContent = 'Scan Deals';
  }
}

function renderResults(city, cuisine) {
  if (allDeals.length === 0) {
    document.getElementById('results').innerHTML = `
      <div class="state">
        <div class="state-icon">😕</div>
        <div class="state-title">No deals found</div>
        <div class="state-sub">Try a different city or remove the cuisine filter</div>
      </div>
    `;
    return;
  }

  const sources = ['All', ...new Set(allDeals.map(d => d.source))];
  const filtered = activeSource === 'All'
    ? allDeals
    : allDeals.filter(d => d.source === activeSource);

  const cuisineTag = cuisine ? ` &bull; <em>${esc(cuisine)}</em>` : '';

  let html = `
    <div class="results-bar">
      <div class="results-title">Deals in ${esc(city)}${cuisineTag}</div>
      <div class="count-badge">${filtered.length} found</div>
    </div>
    <div class="filter-row">
      <span class="filter-label">Source:</span>
  `;

  for (const s of sources) {
    const active = s === activeSource ? ' active' : '';
    html += `<button class="filter-chip${active}" onclick="filterBy('${esc(s)}','${esc(city)}','${esc(cuisine || '')}')">${esc(s)}</button>`;
  }
  html += '</div><div class="deals-grid">';

  for (const d of filtered) {
    const srcClass = 'src-' + d.source.replace(/\s+/g, '');
    const discHtml = d.discount
      ? `<div class="discount-pill">${esc(d.discount)}</div>`
      : '';
    html += `
      <a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer" class="deal-card">
        <div class="source-badge ${srcClass}">${esc(d.source)}</div>
        <div class="deal-body">
          <div class="deal-title">${esc(d.title)}</div>
          ${d.description ? `<div class="deal-desc">${esc(d.description)}</div>` : ''}
        </div>
        ${discHtml}
      </a>
    `;
  }

  html += '</div>';
  document.getElementById('results').innerHTML = html;
}

function filterBy(source, city, cuisine) {
  activeSource = source;
  renderResults(city, cuisine);
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('cuisine').addEventListener('keydown', e => {
    if (e.key === 'Enter') doScan();
  });
});
</script>
</body>
</html>
"""


# ── HTTP Server ────────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/api/search":
            params = parse_qs(parsed.query)
            city = params.get("city", ["Sydney"])[0]
            cuisine = params.get("cuisine", [None])[0] or None

            deals = scan_all(city, cuisine)
            payload = json.dumps(
                {
                    "city": city,
                    "cuisine": cuisine or "",
                    "count": len(deals),
                    "deals": [asdict(d) for d in deals],
                }
            ).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # suppress request logs
        pass


def main():
    print("═" * 50)
    print("  🇦🇺  Australian Restaurant Discount Scanner")
    print("═" * 50)
    print(f"\n  Server starting on http://localhost:{PORT}")
    print("  Press Ctrl+C to stop\n")

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Timer(0.6, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()
