import os
import re
import json
import time
from typing import Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ------------------------------------------------------------
# Config (Railway Variables)
# ------------------------------------------------------------
OZBAG_SOURCE_URL = os.getenv("OZBAG_SOURCE_URL", "https://www.ozbag.com/").strip()
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30").strip())
LOGO_URL = os.getenv("LOGO_URL", "/static/logo.png").strip()

# Margin system (optional)
# You can set MARGINS_JSON as a JSON string in Railway:
# {
#   "ESKI_CEYREK": {"alis_add": 0, "satis_add": 0, "alis_mul": 1.0, "satis_mul": 1.0},
#   "ONS": {"alis_add": 0, "satis_add": 0, "alis_mul": 1.0, "satis_mul": 1.0}
# }
MARGINS_JSON_RAW = os.getenv("MARGINS_JSON", "").strip()

DEFAULT_MARGINS: Dict[str, Dict[str, float]] = {
    # key: {alis_add, satis_add, alis_mul, satis_mul}
    "ESKI_CEYREK": {"alis_add": 0.0, "satis_add": 0.0, "alis_mul": 1.0, "satis_mul": 1.0},
    "ESKI_YARIM":  {"alis_add": 0.0, "satis_add": 0.0, "alis_mul": 1.0, "satis_mul": 1.0},
    "ESKI_TAM":    {"alis_add": 0.0, "satis_add": 0.0, "alis_mul": 1.0, "satis_mul": 1.0},
    "ESKI_GRAMSE": {"alis_add": 0.0, "satis_add": 0.0, "alis_mul": 1.0, "satis_mul": 1.0},
    "ESKI_ATA":    {"alis_add": 0.0, "satis_add": 0.0, "alis_mul": 1.0, "satis_mul": 1.0},
    "ONS":         {"alis_add": 0.0, "satis_add": 0.0, "alis_mul": 1.0, "satis_mul": 1.0},
}

def load_margins() -> Dict[str, Dict[str, float]]:
    margins = dict(DEFAULT_MARGINS)
    if not MARGINS_JSON_RAW:
        return margins
    try:
        user = json.loads(MARGINS_JSON_RAW)
        if isinstance(user, dict):
            for k, v in user.items():
                if isinstance(v, dict):
                    margins[k] = {
                        "alis_add": float(v.get("alis_add", margins.get(k, {}).get("alis_add", 0.0))),
                        "satis_add": float(v.get("satis_add", margins.get(k, {}).get("satis_add", 0.0))),
                        "alis_mul": float(v.get("alis_mul", margins.get(k, {}).get("alis_mul", 1.0))),
                        "satis_mul": float(v.get("satis_mul", margins.get(k, {}).get("satis_mul", 1.0))),
                    }
    except Exception:
        # If margins JSON is broken, ignore and use defaults
        return dict(DEFAULT_MARGINS)
    return margins

MARGINS = load_margins()

# ------------------------------------------------------------
# Product mapping
# ------------------------------------------------------------
# We will scrape Ozbag "Sarrafiye" table:
# Columns usually: Yeni Alış, Yeni Satış, Eski Alış, Eski Satış
# You asked: Eski Çeyrek/Yarım/Tam/Gramse/Ata (alış+satış) + Ons alış+satış
#
# NOTE: "ONS" might be under another section ("Altın Fiyatları") not the Sarrafiye table.
# This app will try to find it anywhere in tables by label matching.
PRODUCTS = [
    # key, display_name, ozbag_row_label, which columns to use
    ("ESKI_CEYREK", "Eski Çeyrek", "ÇEYREK", "ESKI"),
    ("ESKI_YARIM",  "Eski Yarım",  "YARIM",  "ESKI"),
    ("ESKI_TAM",    "Eski Tam",    "TAM",    "ESKI"),
    ("ESKI_GRAMSE", "Eski Gramse", "GRAMSE", "ESKI"),
    ("ESKI_ATA",    "Eski Ata",    "ATA",    "ESKI"),
    ("ONS",         "Ons Altın",   "ONS",    "GENEL"),
]

# ------------------------------------------------------------
# Simple in-memory cache
# ------------------------------------------------------------
_cache: Dict[str, Any] = {
    "ts": 0.0,
    "data": None,       # last good payload
    "source": "YEDEK",  # OZBAG / CACHE / YEDEK
    "error": None
}

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def now_ts() -> float:
    return time.time()

def normalize_label(s: str) -> str:
    s = (s or "").strip().upper()
    # Turkish normalize for common letters
    s = s.replace("İ", "I").replace("İ", "I")
    s = s.replace("Ş", "S").replace("Ğ", "G").replace("Ü", "U").replace("Ö", "O").replace("Ç", "C")
    s = re.sub(r"\s+", " ", s)
    return s

def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    t = text.strip()
    # Keep digits, dot, comma
    t = re.sub(r"[^\d\.,-]", "", t)
    if not t:
        return None
    # Turkish style: 12.150 or 12,150
    # If both exist, assume dot is thousand and comma is decimal (rare here)
    if "," in t and "." in t:
        # remove thousand separator dot
        t = t.replace(".", "")
        # use dot for decimal
        t = t.replace(",", ".")
    else:
        # if comma only, treat as thousand or decimal -> most prices are integers, so remove commas
        if "," in t and "." not in t:
            t = t.replace(",", "")
        # if dot only, treat as thousand separator -> remove dot
        if "." in t and "," not in t:
            # could be decimal, but in TR price tables it's almost always thousand separator
            t = t.replace(".", "")
    try:
        return float(t)
    except Exception:
        return None

def apply_margin(key: str, alis: Optional[float], satis: Optional[float]) -> Dict[str, Optional[float]]:
    m = MARGINS.get(key, {"alis_add": 0.0, "satis_add": 0.0, "alis_mul": 1.0, "satis_mul": 1.0})
    if alis is not None:
        alis = alis * float(m.get("alis_mul", 1.0)) + float(m.get("alis_add", 0.0))
    if satis is not None:
        satis = satis * float(m.get("satis_mul", 1.0)) + float(m.get("satis_add", 0.0))
    return {"alis": alis, "satis": satis}

def fetch_html_requests(url: str, timeout: int = 20) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (TV-Price-App; +https://railway.app/)",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text

def extract_tables(html: str) -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    Return mapping:
    {
      "CEYREK": {"yeni_alis": x, "yeni_satis": y, "eski_alis": a, "eski_satis": b},
      ...
      "ONS": {"alis": x, "satis": y}  (if found in any table as 2-price row)
    }
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    rows_map: Dict[str, Dict[str, Dict[str, float]]] = {}

    for table in tables:
        # read headers
        headers = []
        thead = table.find("thead")
        if thead:
            headers = [normalize_label(th.get_text(" ", strip=True)) for th in thead.find_all(["th", "td"])]
        else:
            # sometimes first row acts as header
            first_tr = table.find("tr")
            if first_tr:
                headers = [normalize_label(x.get_text(" ", strip=True)) for x in first_tr.find_all(["th", "td"])]

        # detect sarrafiye 4 columns style
        # "YENI ALIS", "YENI SATIS", "ESKI ALIS", "ESKI SATIS"
        has_sarrafiye_headers = any("YENI ALIS" in h for h in headers) and any("ESKI SATIS" in h for h in headers)

        for tr in table.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) < 2:
                continue
            label_raw = tds[0].get_text(" ", strip=True)
            label = normalize_label(label_raw)
            if not label:
                continue

            # If sarrafiye-like, expect 5 columns: label + 4 prices
            if has_sarrafiye_headers and len(tds) >= 5:
                yeni_alis = parse_price(tds[1].get_text(" ", strip=True))
                yeni_satis = parse_price(tds[2].get_text(" ", strip=True))
                eski_alis = parse_price(tds[3].get_text(" ", strip=True))
                eski_satis = parse_price(tds[4].get_text(" ", strip=True))
                if any(v is not None for v in [yeni_alis, yeni_satis, eski_alis, eski_satis]):
                    rows_map[label] = {
                        "sarrafiye": {
                            "yeni_alis": yeni_alis,
                            "yeni_satis": yeni_satis,
                            "eski_alis": eski_alis,
                            "eski_satis": eski_satis,
                        }
                    }
            else:
                # Generic 2-price row (for ONS, gram, etc): label + buy + sell
                # Try find first two numeric-like cells after label
                nums = []
                for td in tds[1:]:
                    v = parse_price(td.get_text(" ", strip=True))
                    if v is not None:
                        nums.append(v)
                    if len(nums) >= 2:
                        break
                if len(nums) >= 2:
                    rows_map[label] = {"generic": {"alis": nums[0], "satis": nums[1]}}

    return rows_map

def build_payload(rows_map: Dict[str, Any]) -> Dict[str, Any]:
    out_items = []
    for key, display_name, oz_label, mode in PRODUCTS:
        target = normalize_label(oz_label)

        alis = None
        satis = None

        row = rows_map.get(target)
        if row:
            if mode == "ESKI" and "sarrafiye" in row:
                alis = row["sarrafiye"].get("eski_alis")
                satis = row["sarrafiye"].get("eski_satis")
            elif mode == "GENEL":
                # prefer generic if exists; otherwise try sarrafiye yeni
                if "generic" in row:
                    alis = row["generic"].get("alis")
                    satis = row["generic"].get("satis")
                elif "sarrafiye" in row:
                    alis = row["sarrafiye"].get("yeni_alis")
                    satis = row["sarrafiye"].get("yeni_satis")

        adj = apply_margin(key, alis, satis)

        out_items.append({
            "key": key,
            "name": display_name,
            "alis": adj["alis"],
            "satis": adj["satis"],
        })

    return {
        "ok": True,
        "ts": int(now_ts()),
        "items": out_items,
    }

def get_prices(force: bool = False) -> Dict[str, Any]:
    # Serve fresh if cache valid
    age = now_ts() - float(_cache["ts"] or 0)
    if (not force) and _cache["data"] is not None and age < CACHE_TTL_SECONDS:
        payload = dict(_cache["data"])
        payload["meta"] = {
            "source": "CACHE",
            "cache_age_sec": int(age),
            "last_error": _cache["error"],
            "ozbag_url": OZBAG_SOURCE_URL,
        }
        return payload

    # Try fetch from Ozbag
    try:
        html = fetch_html_requests(OZBAG_SOURCE_URL)
        rows_map = extract_tables(html)
        payload = build_payload(rows_map)

        # If almost everything is None -> treat as failure (page blocked or structure changed)
        none_count = sum(1 for it in payload["items"] if it["alis"] is None and it["satis"] is None)
        if none_count >= len(payload["items"]) - 1:
            raise RuntimeError("Ozbag page parsed but prices not found (structure changed or blocked).")

        _cache["ts"] = now_ts()
        _cache["data"] = payload
        _cache["source"] = "OZBAG"
        _cache["error"] = None

        payload["meta"] = {
            "source": "OZBAG",
            "cache_age_sec": 0,
            "last_error": None,
            "ozbag_url": OZBAG_SOURCE_URL,
        }
        return payload

    except Exception as e:
        # If we have old cache -> serve it
        _cache["error"] = str(e)
        if _cache["data"] is not None:
            payload = dict(_cache["data"])
            payload["meta"] = {
                "source": "CACHE",
                "cache_age_sec": int(now_ts() - float(_cache["ts"] or 0)),
                "last_error": str(e),
                "ozbag_url": OZBAG_SOURCE_URL,
            }
            return payload

        # No cache -> return YEDEK
        return {
            "ok": False,
            "ts": int(now_ts()),
            "items": [{"key": k, "name": n, "alis": None, "satis": None} for k, n, _, _ in PRODUCTS],
            "meta": {
                "source": "YEDEK",
                "cache_age_sec": None,
                "last_error": str(e),
                "ozbag_url": OZBAG_SOURCE_URL,
            }
        }

# ------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------
app = FastAPI(title="Sarıkaya Kuyumculuk TV", version="1.0.0")

# Static folder (put logo.png under ./static/logo.png)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/api/prices")
def api_prices():
    return JSONResponse(get_prices(force=True))

@app.get("/", response_class=HTMLResponse)
def root():
    return tv()

@app.get("/tv", response_class=HTMLResponse)
def tv():
    # Simple TV page with animated price updates
    return HTMLResponse(f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Sarıkaya Kuyumculuk • Canlı Fiyat Ekranı</title>
  <style>
    :root {{
      --bg: #0a0a0d;
      --card: rgba(18,18,22,.78);
      --stroke: rgba(212,175,55,.33);
      --gold: #d4af37;
      --text: #f4f4f6;
      --muted: rgba(244,244,246,.55);
      --ok: #2ecc71;
      --warn: #e74c3c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      background: radial-gradient(1200px 600px at 20% 0%, rgba(212,175,55,.08), transparent 60%),
                  radial-gradient(900px 500px at 100% 10%, rgba(100,100,255,.07), transparent 55%),
                  var(--bg);
      color: var(--text);
    }}
    .wrap {{
      padding: 28px 28px 18px;
      max-width: 1400px;
      margin: 0 auto;
    }}
    .top {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: center;
      margin-bottom: 18px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 14px;
      min-width: 0;
    }}
    .logo {{
      width: 46px;
      height: 46px;
      border-radius: 14px;
      background: rgba(255,255,255,.06);
      border: 1px solid rgba(255,255,255,.08);
      display:flex; align-items:center; justify-content:center;
      overflow:hidden;
    }}
    .logo img {{ width: 100%; height: 100%; object-fit: cover; }}
    .title {{
      min-width: 0;
    }}
    .title h1 {{
      margin: 0;
      font-size: 34px;
      letter-spacing: .6px;
      color: var(--gold);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .title .sub {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 16px;
    }}
    .clock {{
      text-align: right;
    }}
    .clock .t {{
      font-size: 54px;
      font-weight: 700;
      letter-spacing: 1px;
    }}
    .clock .d {{
      color: var(--muted);
      font-size: 18px;
      margin-top: 6px;
    }}

    .grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--stroke);
      border-radius: 26px;
      padding: 18px 18px 16px;
      min-height: 190px;
      position: relative;
      overflow: hidden;
    }}
    .card:before {{
      content:"";
      position:absolute; inset:-1px;
      background: radial-gradient(600px 220px at 30% 0%, rgba(212,175,55,.10), transparent 60%);
      pointer-events:none;
    }}
    .name {{
      position: relative;
      font-size: 20px;
      letter-spacing: 2px;
      color: rgba(244,244,246,.78);
      font-weight: 700;
    }}
    .prices {{
      position: relative;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 14px;
    }}
    .pbox {{
      border-radius: 18px;
      border: 1px solid rgba(255,255,255,.08);
      background: rgba(0,0,0,.12);
      padding: 12px 14px;
    }}
    .plabel {{
      font-size: 12px;
      letter-spacing: 2px;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .pval {{
      font-size: 40px;
      font-weight: 800;
      letter-spacing: .5px;
      display:flex;
      align-items: baseline;
      gap: 8px;
    }}
    .cur {{
      color: var(--gold);
      font-weight: 800;
      font-size: 34px;
    }}
    .meta {{
      position: relative;
      display:flex;
      gap: 12px;
      margin-top: 14px;
      flex-wrap: wrap;
    }}
    .pill {{
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(255,255,255,.06);
      border: 1px solid rgba(255,255,255,.08);
      color: rgba(244,244,246,.78);
      font-size: 14px;
      display:flex;
      gap: 10px;
      align-items:center;
    }}
    .dot {{
      width:10px; height:10px; border-radius:50%;
      background: var(--ok);
    }}
    .dot.warn {{ background: var(--warn); }}
    .small {{
      color: var(--muted);
      font-size: 14px;
      margin-left: auto;
    }}

    /* price animation */
    .flash-up {{ animation: flashUp .65s ease; }}
    .flash-down {{ animation: flashDown .65s ease; }}
    @keyframes flashUp {{
      0% {{ transform: translateY(0); }}
      30% {{ transform: translateY(-2px); }}
      100% {{ transform: translateY(0); }}
    }}
    @keyframes flashDown {{
      0% {{ transform: translateY(0); }}
      30% {{ transform: translateY(2px); }}
      100% {{ transform: translateY(0); }}
    }}

    @media (max-width: 1100px) {{
      .grid {{ grid-template-columns: repeat(2, 1fr); }}
      .clock .t {{ font-size: 44px; }}
    }}
    @media (max-width: 720px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .clock {{ text-align: left; }}
      .top {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo">
          <img src="{LOGO_URL}" onerror="this.style.display='none'"/>
        </div>
        <div class="title">
          <h1>SARIKAYA KUYUMCULUK</h1>
          <div class="sub">Canlı Fiyat Ekranı • TV</div>
        </div>
      </div>
      <div class="clock">
        <div class="t" id="clock">--:--</div>
        <div class="d" id="date">--</div>
      </div>
    </div>

    <div class="grid" id="grid"></div>

    <div class="meta">
      <div class="pill"><span class="dot" id="dot"></span><span id="statusText">Otomatik güncelleniyor</span></div>
      <div class="pill">Kaynak: <b id="source">-</b></div>
      <div class="pill">Son güncelleme: <b id="lastUpdate">-</b></div>
      <div class="pill">Hayırlı işler dileriz</div>
      <div class="small" id="err"></div>
    </div>
  </div>

<script>
  const fmtTRY = (v) => {{
    if (v === null || v === undefined) return "--";
    const n = Math.round(v);
    return n.toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ".");
  }};

  const state = {{
    last: {{}},
    lastTs: 0
  }};

  function setClock() {{
    const d = new Date();
    const hh = String(d.getHours()).padStart(2,'0');
    const mm = String(d.getMinutes()).padStart(2,'0');
    document.getElementById('clock').textContent = `${{hh}}:${{mm}}`;
    const tr = d.toLocaleDateString('tr-TR', {{ weekday:'long', year:'numeric', month:'long', day:'numeric' }});
    document.getElementById('date').textContent = tr.charAt(0).toUpperCase() + tr.slice(1);
  }}
  setInterval(setClock, 1000);
  setClock();

  function cardHTML(item) {{
    const key = item.key;
    return `
      <div class="card" data-key="${{key}}">
        <div class="name">${{item.name}}</div>
        <div class="prices">
          <div class="pbox">
            <div class="plabel">ALIŞ</div>
            <div class="pval" id="alis-${{key}}">-- <span class="cur">₺</span></div>
          </div>
          <div class="pbox">
            <div class="plabel">SATIŞ</div>
            <div class="pval" id="satis-${{key}}">-- <span class="cur">₺</span></div>
          </div>
        </div>
      </div>
    `;
  }}

  function renderGrid(items) {{
    const grid = document.getElementById('grid');
    grid.innerHTML = items.map(cardHTML).join('');
  }}

  function flash(el, dir) {{
    el.classList.remove('flash-up','flash-down');
    void el.offsetWidth; // reflow
    el.classList.add(dir > 0 ? 'flash-up' : 'flash-down');
  }}

  function updateValues(items) {{
    for (const it of items) {{
      const key = it.key;
      const aEl = document.getElementById(`alis-${{key}}`);
      const sEl = document.getElementById(`satis-${{key}}`);

      const prev = state.last[key] || {{}};
      const newA = it.alis;
      const newS = it.satis;

      if (aEl) {{
        aEl.firstChild && (aEl.firstChild.textContent = fmtTRY(newA) + " ");
        if (prev.alis !== undefined && newA !== null && prev.alis !== null && newA !== prev.alis) {{
          flash(aEl, newA > prev.alis ? 1 : -1);
        }}
      }}
      if (sEl) {{
        sEl.firstChild && (sEl.firstChild.textContent = fmtTRY(newS) + " ");
        if (prev.satis !== undefined && newS !== null && prev.satis !== null && newS !== prev.satis) {{
          flash(sEl, newS > prev.satis ? 1 : -1);
        }}
      }}

      state.last[key] = {{ alis: newA, satis: newS }};
    }}
  }}

  async function tick() {{
    try {{
      const res = await fetch('/api/prices', {{ cache: 'no-store' }});
      const data = await res.json();

      if (!document.getElementById('grid').children.length) {{
        renderGrid(data.items);
      }}

      updateValues(data.items);

      const meta = data.meta || {{}};
      document.getElementById('source').textContent = meta.source || '-';
      document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString('tr-TR');
      document.getElementById('err').textContent = meta.last_error ? ("Hata: " + meta.last_error) : "";

      const dot = document.getElementById('dot');
      const statusText = document.getElementById('statusText');
      if (meta.source === "OZBAG") {{
        dot.classList.remove('warn');
        statusText.textContent = "Otomatik güncelleniyor";
      }} else {{
        dot.classList.add('warn');
        statusText.textContent = "Cache (geçici) kullanılıyor";
      }}
    }} catch (e) {{
      const dot = document.getElementById('dot');
      dot.classList.add('warn');
      document.getElementById('statusText').textContent = "Bağlantı hatası";
      document.getElementById('err').textContent = "Hata: " + e;
    }}
  }}

  tick();
  setInterval(tick, 5000);
</script>
</body>
</html>
    """)

# Helpful debug endpoint (optional)
@app.get("/debug", response_class=JSONResponse)
def debug():
    return JSONResponse({
        "OZBAG_SOURCE_URL": OZBAG_SOURCE_URL,
        "CACHE_TTL_SECONDS": CACHE_TTL_SECONDS,
        "LOGO_URL": LOGO_URL,
        "MARGINS": MARGINS,
    })