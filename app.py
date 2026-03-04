import os
import re
import time
import json
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from flask import Flask, jsonify, Response, request, render_template_string, send_from_directory

# =========================
# Config
# =========================
APP_NAME = os.getenv("APP_NAME", "Sarıkaya Kuyumculuk")
OZBAG_SITE_URL = (os.getenv("OZBAG_SITE_URL", "https://www.ozbag.com") or "").strip().rstrip("/")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30") or "30")
LOGO_URL = (os.getenv("LOGO_URL", "/static/logo.png") or "/static/logo.png").strip()
PORT = int(os.getenv("PORT", "8080") or "8080")

# Request tuning
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "8") or "8")
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (compatible; SarikayaPriceBoard/1.0; +https://example.com)"
)

# =========================
# App
# =========================
app = Flask(__name__, static_folder="static", static_url_path="/static")

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})

# In-memory cache
_cache = {
    "ts": 0.0,               # unix seconds
    "data": None,            # last good payload
    "source": "EMPTY",       # OZBAG_JSON | OZBAG_HTML | CACHE | EMPTY
    "error": None,           # last error string
}

# =========================
# Helpers
# =========================
def now_tr():
    # Turkey time display (UTC+3)
    return datetime.now(timezone.utc).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _try_get(url: str) -> requests.Response:
    return _session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)

def _clean_number(s: str):
    """
    Accepts strings like "₺12,150" or "12.150" or "12,150" etc.
    Returns float or None.
    """
    if not s:
        return None
    t = s.strip()
    t = t.replace("₺", "").replace("$", "").replace("€", "")
    t = t.replace("\xa0", " ").strip()

    # Common TR formats:
    # 12.150 or 12,150 (can be thousands)
    # Try to detect if comma is decimal or thousand:
    # In OZBAG screenshot they use comma as thousands? Actually they show "₺12,120" (comma thousands),
    # also sometimes dot as thousands in your UI ("12.150").
    # We'll normalize to digits only, treating last separator carefully.
    # Keep digits and separators
    m = re.findall(r"[0-9]+|[.,]", t)
    if not m:
        return None
    t = "".join(m)

    # If both separators exist, assume last one is decimal if followed by 1-2 digits; else thousands.
    if "." in t and "," in t:
        last_sep = "." if t.rfind(".") > t.rfind(",") else ","
        parts = t.split(last_sep)
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            # decimal
            int_part = re.sub(r"[.,]", "", parts[0])
            dec_part = re.sub(r"[.,]", "", parts[1])
            t2 = f"{int_part}.{dec_part}"
            try:
                return float(t2)
            except:
                return None
        else:
            # thousands
            t2 = re.sub(r"[.,]", "", t)
            try:
                return float(t2)
            except:
                return None

    # Only one separator
    if "," in t and "." not in t:
        # If comma followed by 1-2 digits => decimal, else thousands
        p = t.split(",")
        if len(p) == 2 and len(p[1]) in (1, 2):
            t2 = p[0].replace(",", "") + "." + p[1]
            try:
                return float(t2)
            except:
                return None
        else:
            t2 = t.replace(",", "")
            try:
                return float(t2)
            except:
                return None

    if "." in t and "," not in t:
        # If dot followed by 1-2 digits => decimal, else thousands
        p = t.split(".")
        if len(p) == 2 and len(p[1]) in (1, 2):
            t2 = p[0].replace(".", "") + "." + p[1]
            try:
                return float(t2)
            except:
                return None
        else:
            t2 = t.replace(".", "")
            try:
                return float(t2)
            except:
                return None

    # No separator
    try:
        return float(t)
    except:
        return None

def _format_try(value):
    if value is None:
        return "-"
    # Prefer integer display if looks like integer
    if abs(value - round(value)) < 1e-9:
        v = int(round(value))
        # format with dot thousands (TR-like)
        return f"{v:,}".replace(",", ".")
    return f"{value:.2f}"

def _normalize_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().upper())

# =========================
# OZBAG Parsers
# =========================
def fetch_from_ozbag_json():
    """
    Tries common JSON endpoints. If OZBAG actually provides an API, it'll succeed here.
    If not, raises.
    """
    candidates = [
        f"{OZBAG_SITE_URL}/api/prices",
        f"{OZBAG_SITE_URL}/api/Prices",
        f"{OZBAG_SITE_URL}/prices.json",
        f"{OZBAG_SITE_URL}/data/prices.json",
        f"{OZBAG_SITE_URL}/data.json",
        f"{OZBAG_SITE_URL}/prices",
    ]
    last_err = None
    for url in candidates:
        try:
            r = _try_get(url)
            if r.status_code != 200:
                last_err = f"{url} -> HTTP {r.status_code}"
                continue
            # try json
            data = r.json()
            return data, url
        except Exception as e:
            last_err = f"{url} -> {e}"
            continue
    raise RuntimeError(last_err or "No JSON endpoint worked")

def _parse_sarrafiye_table_from_html(html: str):
    """
    Parses the Sarrafiye table shown in your screenshot (columns include Yeni Alış/Yeni Satış/Eski Alış/Eski Satış).
    We NEED: Eski Alış / Eski Satış for: ÇEYREK, YARIM, TAM, GREMSE, ATA (and optionally ATA BEŞLİ).
    Returns dict.
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError("No tables found in HTML")

    target_table = None
    header_map = None

    # Find a table that has headers like "Yeni Alış" "Eski Alış" etc.
    for t in tables:
        # get header row text
        ths = t.find_all(["th", "td"])
        text_blob = " ".join(_normalize_key(x.get_text(" ", strip=True)) for x in ths[:20])
        if "ESKI" in text_blob or "ESKİ" in text_blob:
            # Try to build header indices from first row
            rows = t.find_all("tr")
            if not rows:
                continue
            first = rows[0].find_all(["th", "td"])
            headers = [_normalize_key(c.get_text(" ", strip=True)) for c in first]
            # Look for required columns
            # We accept both ESKI and ESKİ
            def find_col(name_variants):
                for nv in name_variants:
                    for i, h in enumerate(headers):
                        if nv in h:
                            return i
                return None

            idx_old_buy = find_col(["ESKI ALIS", "ESKİ ALIŞ"])
            idx_old_sell = find_col(["ESKI SATIS", "ESKİ SATIŞ"])
            if idx_old_buy is not None and idx_old_sell is not None:
                target_table = t
                header_map = {"old_buy": idx_old_buy, "old_sell": idx_old_sell}
                break

    if target_table is None:
        raise RuntimeError("Sarrafiye table with 'Eski Alış/Eski Satış' not found")

    rows = target_table.find_all("tr")
    items = {}

    for row in rows[1:]:
        cols = row.find_all(["td", "th"])
        if len(cols) < max(header_map.values()) + 1:
            continue
        name = _normalize_key(cols[0].get_text(" ", strip=True))
        # Some pages may include time under name; keep only first token if needed
        name = name.split("\n")[0].strip()
        if not name:
            continue

        old_buy_txt = cols[header_map["old_buy"]].get_text(" ", strip=True)
        old_sell_txt = cols[header_map["old_sell"]].get_text(" ", strip=True)

        items[name] = {
            "old_buy": _clean_number(old_buy_txt),
            "old_sell": _clean_number(old_sell_txt),
        }

    # Map to requested products
    def pick(key):
        # allow diacritics
        k = _normalize_key(key)
        # direct
        if k in items:
            return items[k]
        # fuzzy
        for kk in items.keys():
            if k in kk or kk in k:
                return items[kk]
        return {"old_buy": None, "old_sell": None}

    result = {
        "CEYREK": pick("ÇEYREK"),
        "YARIM": pick("YARIM"),
        "TAM": pick("TAM"),
        "GREMSE": pick("GREMSE"),
        "ATA": pick("ATA"),
        "ATA_BESLI": pick("ATA BEŞLİ"),
    }
    return result

def _parse_ons_from_html(html: str):
    """
    Tries to find Ons Altın (ONS) buy/sell somewhere in the page.
    Returns {"buy": float|None, "sell": float|None}
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    # If page contains a row-like structure for ONS, try tables first
    tables = soup.find_all("table")
    for t in tables:
        rows = t.find_all("tr")
        for row in rows:
            cols = row.find_all(["td", "th"])
            if len(cols) < 3:
                continue
            name = _normalize_key(cols[0].get_text(" ", strip=True))
            if "ONS" in name:
                # Try next 2 columns as buy/sell
                buy = _clean_number(cols[1].get_text(" ", strip=True))
                sell = _clean_number(cols[2].get_text(" ", strip=True))
                if buy is not None or sell is not None:
                    return {"buy": buy, "sell": sell}

    # fallback: regex on text
    # very conservative, may fail silently (that's ok)
    m = re.search(r"ONS[^0-9]{0,20}([0-9\.,]+)[^0-9]{0,20}([0-9\.,]+)", text.upper())
    if m:
        return {"buy": _clean_number(m.group(1)), "sell": _clean_number(m.group(2))}
    return {"buy": None, "sell": None}

def fetch_from_ozbag_html():
    """
    Fetches OZBAG site HTML and scrapes.
    We try a few likely pages.
    """
    candidates = [
        f"{OZBAG_SITE_URL}/sarrafiye",
        f"{OZBAG_SITE_URL}/Sarrafiye",
        f"{OZBAG_SITE_URL}/altin-fiyatlari",
        f"{OZBAG_SITE_URL}/Altin-Fiyatlari",
        f"{OZBAG_SITE_URL}/",
    ]
    last_err = None
    for url in candidates:
        try:
            r = _try_get(url)
            if r.status_code != 200:
                last_err = f"{url} -> HTTP {r.status_code}"
                continue
            html = r.text
            sarrafiye = _parse_sarrafiye_table_from_html(html)
            ons = _parse_ons_from_html(html)
            return {"sarrafiye": sarrafiye, "ons": ons}, url
        except Exception as e:
            last_err = f"{url} -> {e}"
            continue
    raise RuntimeError(last_err or "No HTML page could be parsed")

# =========================
# Core fetch with caching
# =========================
def get_prices(force: bool = False):
    now = time.time()
    fresh = (_cache["data"] is not None) and (now - _cache["ts"] < CACHE_TTL_SECONDS)
    if fresh and not force:
        payload = dict(_cache["data"])
        payload["meta"]["source"] = "CACHE"
        payload["meta"]["cache_age_sec"] = int(now - _cache["ts"])
        return payload

    # Try JSON first, then HTML scrape
    try:
        # If JSON structure is unknown, we still keep it as debug, but prefer HTML path.
        # We'll attempt JSON, but if it doesn't contain what we need, fallback.
        jdata, jurl = fetch_from_ozbag_json()
        # If json has expected keys, map them (optional)
        # We don't assume structure; fallback to html scrape for reliability.
        raise RuntimeError(f"JSON endpoint reachable but mapping unknown: {jurl}")
    except Exception:
        pass

    try:
        hdata, hurl = fetch_from_ozbag_html()

        sar = hdata["sarrafiye"]
        ons = hdata["ons"]

        payload = {
            "meta": {
                "app": APP_NAME,
                "updated_at": datetime.now().strftime("%H:%M:%S"),
                "date": datetime.now().strftime("%d %B %Y"),
                "source": "OZBAG_HTML",
                "source_url": hurl,
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
                "cache_age_sec": 0,
                "error": None,
            },
            "products": {
                # requested "Eski" prices (old buy/sell)
                "ESKI_CEYREK": {"buy": sar["CEYREK"]["old_buy"], "sell": sar["CEYREK"]["old_sell"]},
                "ESKI_YARIM": {"buy": sar["YARIM"]["old_buy"], "sell": sar["YARIM"]["old_sell"]},
                "ESKI_TAM": {"buy": sar["TAM"]["old_buy"], "sell": sar["TAM"]["old_sell"]},
                "ESKI_GREMSE": {"buy": sar["GREMSE"]["old_buy"], "sell": sar["GREMSE"]["old_sell"]},
                "ESKI_ATA": {"buy": sar["ATA"]["old_buy"], "sell": sar["ATA"]["old_sell"]},
                # ons
                "ONS_ALTIN": {"buy": ons.get("buy"), "sell": ons.get("sell")},
            },
        }

        # Save cache
        _cache["ts"] = now
        _cache["data"] = payload
        _cache["source"] = "OZBAG_HTML"
        _cache["error"] = None
        return payload

    except Exception as e:
        # Hard fail -> serve last cache if exists
        err = str(e)
        _cache["error"] = err

        if _cache["data"] is not None:
            payload = dict(_cache["data"])
            payload["meta"] = dict(payload.get("meta", {}))
            payload["meta"]["source"] = "CACHE"
            payload["meta"]["error"] = err
            payload["meta"]["cache_age_sec"] = int(now - _cache["ts"])
            return payload

        # No cache at all
        return {
            "meta": {
                "app": APP_NAME,
                "updated_at": datetime.now().strftime("%H:%M:%S"),
                "date": datetime.now().strftime("%d %B %Y"),
                "source": "EMPTY",
                "source_url": None,
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
                "cache_age_sec": None,
                "error": err,
            },
            "products": {},
        }

# =========================
# Routes
# =========================
@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "app": APP_NAME,
        "ozbag_site_url": OZBAG_SITE_URL,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "cache_has_data": _cache["data"] is not None,
        "last_error": _cache["error"],
    })

@app.get("/api/prices")
def api_prices():
    force = request.args.get("force") == "1"
    payload = get_prices(force=force)
    return jsonify(payload)

@app.get("/")
def home():
    return Response(
        '<meta http-equiv="refresh" content="0; url=/tv">',
        mimetype="text/html"
    )

@app.get("/tv")
def tv():
    # TV UI (polls /api/prices)
    html = f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{APP_NAME} • Canlı Fiyat Ekranı</title>
  <style>
    :root {{
      --bg: #0b0b0f;
      --card: rgba(255,255,255,0.05);
      --border: rgba(218, 165, 32, 0.35);
      --gold: #d4af37;
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.55);
      --good: #2ecc71;
      --bad: #ff4d4d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(1200px 800px at 20% 10%, rgba(212,175,55,0.10), transparent 60%),
                  radial-gradient(1200px 800px at 80% 40%, rgba(212,175,55,0.08), transparent 60%),
                  var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, Arial, sans-serif;
    }}
    .wrap {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 28px 22px 36px;
    }}
    .top {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 14px;
      min-width: 320px;
    }}
    .logo {{
      width: 54px; height: 54px;
      border-radius: 14px;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.08);
      display: grid;
      place-items: center;
      overflow: hidden;
    }}
    .logo img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
    .title {{
      line-height: 1.05;
    }}
    .title h1 {{
      margin: 0;
      font-size: 34px;
      letter-spacing: 1px;
      color: var(--gold);
      font-weight: 800;
    }}
    .title .sub {{
      margin-top: 6px;
      font-size: 16px;
      color: var(--muted);
    }}
    .clock {{
      text-align: right;
      line-height: 1.05;
    }}
    .clock .time {{
      font-size: 64px;
      font-weight: 800;
      letter-spacing: 1px;
    }}
    .clock .date {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 18px;
    }}

    .grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
      margin-top: 18px;
    }}
    .card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03));
      border: 1px solid var(--border);
      border-radius: 22px;
      padding: 18px 18px 14px;
      min-height: 160px;
      position: relative;
      overflow: hidden;
    }}
    .card:before {{
      content: "";
      position: absolute;
      inset: -40%;
      background: radial-gradient(circle at 30% 20%, rgba(212,175,55,0.10), transparent 50%);
      transform: rotate(10deg);
      pointer-events: none;
    }}
    .label {{
      font-size: 18px;
      letter-spacing: 2px;
      color: rgba(255,255,255,0.70);
      font-weight: 700;
      position: relative;
    }}
    .prices {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 14px;
      position: relative;
    }}
    .pill {{
      background: rgba(0,0,0,0.20);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 16px;
      padding: 10px 12px;
    }}
    .pill .k {{
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 1px;
    }}
    .pill .v {{
      margin-top: 6px;
      font-size: 34px;
      font-weight: 900;
      display: flex;
      align-items: baseline;
      gap: 8px;
    }}
    .pill .v .cur {{
      color: var(--gold);
      font-size: 26px;
      font-weight: 900;
    }}
    .updated {{
      margin-top: 10px;
      color: rgba(255,255,255,0.45);
      font-size: 13px;
      position: relative;
    }}

    .footer {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-top: 18px;
      flex-wrap: wrap;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.04);
      color: rgba(255,255,255,0.75);
    }}
    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--good);
    }}
    .dot.bad {{
      background: var(--bad);
    }}

    /* price animation */
    .flash-up {{
      animation: flashUp 0.65s ease-in-out;
    }}
    .flash-down {{
      animation: flashDown 0.65s ease-in-out;
    }}
    @keyframes flashUp {{
      0% {{ transform: scale(1); color: var(--text); }}
      35% {{ transform: scale(1.03); color: var(--good); }}
      100% {{ transform: scale(1); color: var(--text); }}
    }}
    @keyframes flashDown {{
      0% {{ transform: scale(1); color: var(--text); }}
      35% {{ transform: scale(1.03); color: var(--bad); }}
      100% {{ transform: scale(1); color: var(--text); }}
    }}

    @media (max-width: 980px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .clock .time {{ font-size: 46px; }}
      .title h1 {{ font-size: 28px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo" id="logoBox">
          <img src="{LOGO_URL}" alt="logo" onerror="this.style.display='none'; document.getElementById('logoBox').innerHTML='<div style=\\'font-weight:900;color:#d4af37;\\'>SK</div>';">
        </div>
        <div class="title">
          <h1>{APP_NAME.upper()}</h1>
          <div class="sub">Canlı Fiyat Ekranı • TV</div>
        </div>
      </div>

      <div class="clock">
        <div class="time" id="time">--:--</div>
        <div class="date" id="date">--</div>
      </div>
    </div>

    <div class="grid" id="grid"></div>

    <div class="footer">
      <div class="chip">
        <span class="dot" id="dot"></span>
        <span id="statusText">Otomatik güncelleniyor</span>
      </div>
      <div class="chip">Kaynak: <b id="source">-</b></div>
      <div class="chip">Son güncelleme: <b id="last">-</b></div>
      <div class="chip">Hayırlı işler dileriz</div>
    </div>
  </div>

<script>
  const PRODUCTS = [
    {{ key: "ESKI_CEYREK", title: "ESKİ ÇEYREK" }},
    {{ key: "ESKI_YARIM",  title: "ESKİ YARIM" }},
    {{ key: "ESKI_TAM",    title: "ESKİ TAM" }},
    {{ key: "ESKI_GREMSE", title: "ESKİ GREMSE" }},
    {{ key: "ESKI_ATA",    title: "ESKİ ATA" }},
    {{ key: "ONS_ALTIN",   title: "ONS ALTIN" }},
  ];

  const fmt = (n) => {{
    if (n === null || n === undefined) return "-";
    const isInt = Math.abs(n - Math.round(n)) < 1e-9;
    if (isInt) {{
      return new Intl.NumberFormat('tr-TR').format(Math.round(n));
    }}
    return new Intl.NumberFormat('tr-TR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }}).format(n);
  }};

  function tickClock() {{
    const d = new Date();
    const hh = String(d.getHours()).padStart(2,'0');
    const mm = String(d.getMinutes()).padStart(2,'0');
    document.getElementById('time').textContent = `${{hh}}:${{mm}}`;
    document.getElementById('date').textContent =
      d.toLocaleDateString('tr-TR', {{ day:'2-digit', month:'long', year:'numeric', weekday:'long' }});
  }}
  setInterval(tickClock, 1000);
  tickClock();

  function cardTemplate(p, buy, sell) {{
    return `
      <div class="card" data-key="${{p.key}}">
        <div class="label">${{p.title}}</div>
        <div class="prices">
          <div class="pill">
            <div class="k">ALIŞ</div>
            <div class="v"><span class="num" data-side="buy">${{fmt(buy)}}</span> <span class="cur">₺</span></div>
          </div>
          <div class="pill">
            <div class="k">SATIŞ</div>
            <div class="v"><span class="num" data-side="sell">${{fmt(sell)}}</span> <span class="cur">₺</span></div>
          </div>
        </div>
        <div class="updated">Güncelleme: <span class="u">--</span></div>
      </div>
    `;
  }}

  function buildGrid(initial) {{
    const grid = document.getElementById('grid');
    grid.innerHTML = PRODUCTS.map(p => {{
      const obj = (initial && initial[p.key]) ? initial[p.key] : {{buy:null, sell:null}};
      return cardTemplate(p, obj.buy, obj.sell);
    }}).join('');
  }}

  function animateIfChanged(el, prev, next) {{
    if (prev === null || prev === undefined || next === null || next === undefined) return;
    if (prev === next) return;
    el.classList.remove('flash-up', 'flash-down');
    void el.offsetWidth;
    el.classList.add(next > prev ? 'flash-up' : 'flash-down');
  }}

  function loadPrev() {{
    try {{ return JSON.parse(localStorage.getItem("prevPrices") || "{{}}"); }} catch(e) {{ return {{}}; }}
  }}
  function savePrev(obj) {{
    try {{ localStorage.setItem("prevPrices", JSON.stringify(obj)); }} catch(e) {{}}
  }}

  async function refresh() {{
    const dot = document.getElementById('dot');
    const statusText = document.getElementById('statusText');
    try {{
      const r = await fetch('/api/prices', {{ cache: 'no-store' }});
      const data = await r.json();

      const prev = loadPrev();
      const nextStore = {{}};

      document.getElementById('source').textContent = data.meta.source || "-";
      document.getElementById('last').textContent = data.meta.updated_at || "-";

      const ok = !data.meta.error;
      dot.classList.toggle('bad', !ok);
      statusText.textContent = ok ? "Otomatik güncelleniyor" : ("Uyarı: " + (data.meta.error || "Hata"));

      for (const p of PRODUCTS) {{
        const card = document.querySelector(`.card[data-key="${{p.key}}"]`);
        if (!card) continue;

        const obj = (data.products && data.products[p.key]) ? data.products[p.key] : {{buy:null, sell:null}};
        const buyEl = card.querySelector('.num[data-side="buy"]');
        const sellEl = card.querySelector('.num[data-side="sell"]');
        const uEl = card.querySelector('.u');

        const prevBuy = (prev[p.key] && prev[p.key].buy !== undefined) ? prev[p.key].buy : null;
        const prevSell = (prev[p.key] && prev[p.key].sell !== undefined) ? prev[p.key].sell : null;

        // update text
        buyEl.textContent = fmt(obj.buy);
        sellEl.textContent = fmt(obj.sell);

        // animate
        animateIfChanged(buyEl, prevBuy, obj.buy);
        animateIfChanged(sellEl, prevSell, obj.sell);

        uEl.textContent = data.meta.updated_at || "--";

        nextStore[p.key] = {{ buy: obj.buy, sell: obj.sell }};
      }}

      savePrev(nextStore);
    }} catch (e) {{
      dot.classList.add('bad');
      statusText.textContent = "Bağlantı hatası";
    }}
  }}

  buildGrid();
  refresh();
  setInterval(refresh, {max(5, CACHE_TTL_SECONDS)} * 1000);
</script>
</body>
</html>
"""
    return render_template_string(html)

# Serve static folder safely (optional)
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    # Railway will set PORT; bind 0.0.0.0
    app.run(host="0.0.0.0", port=PORT)