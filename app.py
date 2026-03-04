import os
import re
import time
import json
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Any, Tuple, List

import requests
from flask import Flask, jsonify, Response

# BeautifulSoup opsiyonel; yoksa regex fallback var
try:
    from bs4 import BeautifulSoup  # type: ignore
    HAS_BS4 = True
except Exception:
    HAS_BS4 = False

app = Flask(__name__)

# -----------------------
# CONFIG
# -----------------------
OZBAG_SCRAPE_URL = os.getenv("OZBAG_SCRAPE_URL", "https://www.ozbag.com/").strip()
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "20").strip() or "20")
LOGO_URL = os.getenv("LOGO_URL", "/static/logo.png").strip()
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "8").strip() or "8")
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (compatible; SarikayaPriceScreen/1.0; +https://example.com)"
)

# İleride ürün eklemek: sadece buraya yeni satır ekle
# label_patterns: sayfadaki satır adlarını yakalamak için alternatif isimler
PRODUCTS = [
    {
        "key": "ceyrek",
        "title": "ÇEYREK",
        "label_patterns": ["ÇEYREK"],
        "type": "sarrafiye",
    },
    {
        "key": "yarim",
        "title": "YARIM",
        "label_patterns": ["YARIM"],
        "type": "sarrafiye",
    },
    {
        "key": "tam",
        "title": "TAM",
        "label_patterns": ["TAM"],
        "type": "sarrafiye",
    },
    {
        "key": "gremse",
        "title": "GREMSE",
        "label_patterns": ["GREMSE", "GRAMSE", "GREMS(E)"],
        "type": "sarrafiye",
    },
    {
        "key": "ata",
        "title": "ATA",
        "label_patterns": ["ATA"],
        "type": "sarrafiye",
    },
    {
        "key": "ons",
        "title": "ONS",
        "label_patterns": ["ONS", "ONS ALTIN", "ONS ALTIN ($)", "XAU/USD"],
        "type": "ons",
    },
]

# -----------------------
# CACHE
# -----------------------
_cache_lock = threading.Lock()
_cache_data: Optional[Dict[str, Any]] = None
_cache_ts: float = 0.0
_cache_source: str = "CACHE"


def now_ts() -> float:
    return time.time()


def is_cache_valid() -> bool:
    global _cache_ts
    if _cache_data is None:
        return False
    return (now_ts() - _cache_ts) <= CACHE_TTL_SECONDS


def set_cache(data: Dict[str, Any], source: str) -> None:
    global _cache_data, _cache_ts, _cache_source
    _cache_data = data
    _cache_ts = now_ts()
    _cache_source = source


def get_cache() -> Tuple[Optional[Dict[str, Any]], float, str]:
    return _cache_data, _cache_ts, _cache_source


# -----------------------
# HELPERS
# -----------------------
def tr_format_int(n: Optional[int]) -> str:
    """12150 -> '12.150'"""
    if n is None:
        return "—"
    s = f"{n:,}".replace(",", ".")
    return s


def tr_format_float(n: Optional[float], decimals: int = 2) -> str:
    """float -> TR format with comma decimals (optional)"""
    if n is None:
        return "—"
    s = f"{n:,.{decimals}f}"
    # 12,345.67 -> 12.345,67
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def parse_money_to_int(text: str) -> Optional[int]:
    """
    '₺12,120' or '12.120' or '12,120' -> 12120
    """
    if not text:
        return None
    t = text.strip()
    t = t.replace("₺", "").replace("TL", "").replace("TRY", "")
    t = t.replace("\xa0", " ").strip()
    # some pages use comma for thousand (12,120) and some dot (12.120)
    # normalize: keep digits only
    digits = re.sub(r"[^\d]", "", t)
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def parse_ons_to_float(text: str) -> Optional[float]:
    """
    ONS may appear like '2,123.45' or '2.123,45' or '2123.45'
    We'll try to detect both.
    """
    if not text:
        return None
    t = text.strip()
    t = t.replace("$", "").replace("USD", "").replace("XAU", "")
    t = t.replace("\xa0", " ").strip()

    # if contains both '.' and ',' assume TR style "2.123,45"
    if "." in t and "," in t:
        # TR -> remove thousand dots, comma decimal
        t2 = t.replace(".", "").replace(",", ".")
        try:
            return float(re.sub(r"[^\d.]", "", t2))
        except Exception:
            return None

    # if only ',' maybe decimal or thousand; try both
    if "," in t and "." not in t:
        # If last comma has 2 digits -> decimal
        parts = t.split(",")
        if len(parts[-1]) in (1, 2, 3):
            # could be decimal; try as decimal first
            try:
                return float(re.sub(r"[^\d.]", "", t.replace(",", ".")))
            except Exception:
                pass
        # fallback digits-only
        digits = re.sub(r"[^\d]", "", t)
        if not digits:
            return None
        try:
            return float(digits)
        except Exception:
            return None

    # only '.' or none -> normal float
    try:
        return float(re.sub(r"[^\d.]", "", t))
    except Exception:
        return None


def fetch_html(url: str) -> str:
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def normalize_label(s: str) -> str:
    s = (s or "").strip().upper()
    s = s.replace("İ", "I")
    s = re.sub(r"\s+", " ", s)
    return s


def match_label(label: str, patterns: List[str]) -> bool:
    L = normalize_label(label)
    for p in patterns:
        p2 = normalize_label(p)
        # allow regex-like patterns if user put parentheses in list
        try:
            if re.search(p2, L):
                return True
        except Exception:
            if p2 in L:
                return True
    return False


# -----------------------
# PARSING (BS4 preferred)
# -----------------------
def extract_prices_bs4(html: str) -> Dict[str, Any]:
    """
    Tries to find a table containing columns:
    Yeni Alış, Yeni Satış, Eski Alış, Eski Satış
    And rows with product labels.
    Also tries to find ONS buy/sell in any table-like structure.
    """
    soup = BeautifulSoup(html, "html.parser")
    result: Dict[str, Any] = {}

    # Helper: find tables
    tables = soup.find_all("table")
    # If site uses div-table, also include all elements that look like rows
    # but we'll start with real <table>.
    def parse_table(table) -> None:
        # Find header columns
        headers = [normalize_label(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        # Sometimes headers are in first row td
        if not headers:
            first_tr = table.find("tr")
            if first_tr:
                headers = [normalize_label(td.get_text(" ", strip=True)) for td in first_tr.find_all(["td", "th"])]

        # map column indexes
        col_map = {}
        for idx, h in enumerate(headers):
            if "YENI ALIS" in h or "YENİ ALIŞ" in h:
                col_map["new_buy"] = idx
            if "YENI SATIS" in h or "YENİ SATIŞ" in h:
                col_map["new_sell"] = idx
            if "ESKI ALIS" in h or "ESKİ ALIŞ" in h:
                col_map["old_buy"] = idx
            if "ESKI SATIS" in h or "ESKİ SATIŞ" in h:
                col_map["old_sell"] = idx
            if h in ("ALIS", "ALIŞ"):
                col_map.setdefault("buy", idx)
            if h in ("SATIS", "SATIŞ"):
                col_map.setdefault("sell", idx)

        # parse rows
        rows = table.find_all("tr")
        for tr in rows:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            if not label:
                continue

            # Match sarrafiye products
            for p in PRODUCTS:
                if p["type"] != "sarrafiye":
                    continue
                if match_label(label, p["label_patterns"]):
                    # expect cols exist
                    def get_cell(i: Optional[int]) -> str:
                        if i is None:
                            return ""
                        if i >= len(cells):
                            return ""
                        return cells[i].get_text(" ", strip=True)

                    item = {
                        "new_buy": parse_money_to_int(get_cell(col_map.get("new_buy"))),
                        "new_sell": parse_money_to_int(get_cell(col_map.get("new_sell"))),
                        "old_buy": parse_money_to_int(get_cell(col_map.get("old_buy"))),
                        "old_sell": parse_money_to_int(get_cell(col_map.get("old_sell"))),
                    }
                    result[p["key"]] = item

            # Match ons
            for p in PRODUCTS:
                if p["type"] != "ons":
                    continue
                if match_label(label, p["label_patterns"]):
                    # ons may be float
                    buy_i = col_map.get("buy")
                    sell_i = col_map.get("sell")
                    if buy_i is None and len(cells) >= 3:
                        buy_i = 1
                    if sell_i is None and len(cells) >= 3:
                        sell_i = 2

                    buy_txt = cells[buy_i].get_text(" ", strip=True) if buy_i is not None and buy_i < len(cells) else ""
                    sell_txt = cells[sell_i].get_text(" ", strip=True) if sell_i is not None and sell_i < len(cells) else ""

                    result[p["key"]] = {
                        "buy": parse_ons_to_float(buy_txt),
                        "sell": parse_ons_to_float(sell_txt),
                    }

    for t in tables:
        parse_table(t)

    # Fallback: sometimes there is no <table> but a grid.
    # We'll regex scan the whole HTML for rows of the sarrafiye table.
    if not all(k in result for k in ["ceyrek", "yarim", "tam", "gremse", "ata"]):
        regex_result = extract_prices_regex(html)
        # only fill missing keys
        for k, v in regex_result.items():
            result.setdefault(k, v)

    return result


def extract_prices_regex(html: str) -> Dict[str, Any]:
    """
    Regex fallback:
    Tries to capture rows like:
    ÇEYREK ... ₺12,120 ... ₺12,285 ... ₺11,955 ... ₺12,150
    """
    out: Dict[str, Any] = {}
    # Make whitespace manageable
    h = re.sub(r"\s+", " ", html)

    def capture_sarrafiye(label_patterns: List[str]) -> Optional[Dict[str, int]]:
        for lp in label_patterns:
            # take row chunk after label up to next row-ish keyword
            # We look for 4 money fields after the label.
            pattern = rf"({lp}).{{0,200}}?₺\s*([\d\.,]+).{{0,200}}?₺\s*([\d\.,]+).{{0,200}}?₺\s*([\d\.,]+).{{0,200}}?₺\s*([\d\.,]+)"
            m = re.search(pattern, h, re.IGNORECASE)
            if m:
                return {
                    "new_buy": parse_money_to_int(m.group(2)) or None,
                    "new_sell": parse_money_to_int(m.group(3)) or None,
                    "old_buy": parse_money_to_int(m.group(4)) or None,
                    "old_sell": parse_money_to_int(m.group(5)) or None,
                }
        return None

    for p in PRODUCTS:
        if p["type"] == "sarrafiye":
            item = capture_sarrafiye(p["label_patterns"])
            if item:
                out[p["key"]] = item

    # ons fallback (buy/sell as float)
    # try to capture: ONS ... 2,123.45 ... 2,124.10
    ons_pat = r"(ONS|ONS ALTIN|XAU\/USD).{0,250}?([\d\.,]+).{0,120}?([\d\.,]+)"
    m = re.search(ons_pat, h, re.IGNORECASE)
    if m:
        out["ons"] = {"buy": parse_ons_to_float(m.group(2)), "sell": parse_ons_to_float(m.group(3))}
    return out


def scrape_ozbag() -> Dict[str, Any]:
    html = fetch_html(OZBAG_SCRAPE_URL)
    if HAS_BS4:
        data = extract_prices_bs4(html)
    else:
        data = extract_prices_regex(html)

    # Validate minimal set: if nothing, raise
    if not data:
        raise RuntimeError("Özbağ sayfasından veri çıkarılamadı (tablo bulunamadı).")

    return data


def build_payload(data: Dict[str, Any], source: str, cache_used: bool, error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "ok": True if data else False,
        "source": source,
        "cache_used": cache_used,
        "ts": int(now_ts()),
        "url": OZBAG_SCRAPE_URL,
        "error": error,
        "data": data,
    }


def get_prices() -> Dict[str, Any]:
    with _cache_lock:
        if is_cache_valid():
            cd, cts, csrc = get_cache()
            if cd:
                return build_payload(cd, csrc, cache_used=True)

    # cache invalid -> try scrape
    try:
        fresh = scrape_ozbag()
        with _cache_lock:
            set_cache(fresh, source="OZBAG")
        return build_payload(fresh, "OZBAG", cache_used=False)
    except Exception as e:
        # fallback to cache even if stale
        with _cache_lock:
            cd, cts, csrc = get_cache()
            if cd:
                return build_payload(cd, csrc, cache_used=True, error=str(e))
        # no cache at all
        return {
            "ok": False,
            "source": "NONE",
            "cache_used": False,
            "ts": int(now_ts()),
            "url": OZBAG_SCRAPE_URL,
            "error": str(e),
            "data": {},
        }


# -----------------------
# ROUTES
# -----------------------
@app.get("/health")
def health():
    return jsonify({"ok": True, "ts": int(now_ts())})


@app.get("/prices")
def prices():
    payload = get_prices()
    status = 200 if payload.get("ok") else 502
    return jsonify(payload), status


@app.get("/")
@app.get("/tv")
def tv():
    # Single-page TV view. JS fetches /prices periodically and animates changes.
    # Layout is responsive; TV should be opened full-screen in landscape.
    html = f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>SARIKAYA KUYUMCULUK - Canlı Fiyat Ekranı</title>
  <style>
    :root {{
      --bg: #0b0f14;
      --panel: rgba(255,255,255,0.05);
      --panel2: rgba(255,255,255,0.03);
      --gold: #c9a24a;
      --text: #e9eef5;
      --muted: rgba(233,238,245,0.65);
      --ok: #22c55e;
      --down: #ef4444;
      --stroke: rgba(201,162,74,0.35);
    }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial;
      background: radial-gradient(1200px 800px at 10% 10%, rgba(201,162,74,0.15), transparent 55%),
                  radial-gradient(900px 700px at 90% 20%, rgba(100,140,255,0.10), transparent 60%),
                  var(--bg);
      color: var(--text);
      overflow: hidden;
    }}

    .wrap {{
      padding: 28px 34px;
      height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 18px;
    }}

    .top {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      align-items: center;
      gap: 18px;
    }}

    .brand {{
      display: flex;
      align-items: center;
      gap: 14px;
      min-width: 320px;
    }}

    .logo {{
      width: 56px;
      height: 56px;
      border-radius: 16px;
      border: 1px solid var(--stroke);
      background: var(--panel2);
      display: grid;
      place-items: center;
      overflow: hidden;
    }}
    .logo img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
    .brand h1 {{
      margin: 0;
      font-size: 36px;
      letter-spacing: 2px;
      font-weight: 800;
      color: var(--gold);
      line-height: 1.05;
    }}
    .brand .sub {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 16px;
      letter-spacing: .3px;
    }}

    .clock {{
      text-align: right;
    }}
    .clock .time {{
      font-size: 56px;
      font-weight: 900;
      letter-spacing: 2px;
    }}
    .clock .date {{
      margin-top: 6px;
      font-size: 20px;
      color: var(--muted);
      letter-spacing: .4px;
    }}

    .grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
      align-content: start;
    }}

    .card {{
      position: relative;
      background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03));
      border: 1px solid var(--stroke);
      border-radius: 26px;
      padding: 22px 24px;
      min-height: 170px;
      overflow: hidden;
      box-shadow: 0 18px 60px rgba(0,0,0,0.35);
    }}
    .card::before {{
      content:"";
      position:absolute;
      inset:-40px -80px auto auto;
      width: 260px;
      height: 260px;
      background: radial-gradient(circle at 30% 30%, rgba(201,162,74,0.18), transparent 60%);
      transform: rotate(12deg);
    }}

    .title {{
      font-size: 20px;
      letter-spacing: 3px;
      font-weight: 800;
      color: rgba(233,238,245,0.80);
    }}

    .rows {{
      margin-top: 16px;
      display: grid;
      gap: 8px;
    }}

    .row {{
      display: grid;
      grid-template-columns: 130px 1fr 1fr;
      align-items: baseline;
      gap: 12px;
      padding: 10px 0;
      border-top: 1px solid rgba(255,255,255,0.06);
    }}
    .row:first-child {{
      border-top: none;
      padding-top: 0;
    }}
    .row .lbl {{
      color: rgba(233,238,245,0.55);
      font-weight: 700;
      letter-spacing: 1px;
      font-size: 13px;
      text-transform: uppercase;
    }}

    .val {{
      font-size: 34px;
      font-weight: 900;
      letter-spacing: 1px;
      display: inline-flex;
      align-items: center;
      gap: 10px;
    }}
    .val small {{
      font-size: 22px;
      color: var(--gold);
      font-weight: 900;
    }}

    .meta {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      color: var(--muted);
      font-size: 16px;
    }}

    .pill {{
      display:inline-flex;
      align-items:center;
      gap: 10px;
      background: rgba(0,0,0,0.35);
      border: 1px solid rgba(255,255,255,0.08);
      padding: 10px 14px;
      border-radius: 999px;
      backdrop-filter: blur(6px);
    }}

    .dot {{
      width: 10px; height: 10px; border-radius: 999px;
      background: var(--ok);
      box-shadow: 0 0 12px rgba(34,197,94,0.45);
    }}
    .dot.bad {{
      background: var(--down);
      box-shadow: 0 0 12px rgba(239,68,68,0.45);
    }}

    /* Animation states */
    .up {{
      animation: upPulse 750ms ease-out;
    }}
    .down {{
      animation: downPulse 750ms ease-out;
    }}
    @keyframes upPulse {{
      0% {{ transform: scale(1.00); color: var(--text); }}
      20% {{ transform: scale(1.03); color: var(--ok); }}
      100% {{ transform: scale(1.00); color: var(--text); }}
    }}
    @keyframes downPulse {{
      0% {{ transform: scale(1.00); color: var(--text); }}
      20% {{ transform: scale(1.03); color: var(--down); }}
      100% {{ transform: scale(1.00); color: var(--text); }}
    }}

    .arrow {{
      font-size: 18px;
      font-weight: 900;
      opacity: 0.9;
    }}
    .arrow.up {{ color: var(--ok); }}
    .arrow.down {{ color: var(--down); }}

    /* Responsive */
    @media (max-width: 1100px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .clock .time {{ font-size: 44px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo" id="logoBox">
          <img src="{LOGO_URL}" alt="SK" onerror="this.style.display='none';document.getElementById('logoBox').textContent='SK';document.getElementById('logoBox').style.fontWeight='900';document.getElementById('logoBox').style.color='white';"/>
        </div>
        <div>
          <h1>SARIKAYA KUYUMCULUK</h1>
          <div class="sub">Canlı Fiyat Ekranı • TV</div>
        </div>
      </div>

      <div></div>

      <div class="clock">
        <div class="time" id="time">--:--</div>
        <div class="date" id="date">--</div>
      </div>
    </div>

    <div class="grid" id="grid">
      <!-- Cards injected by JS -->
    </div>

    <div class="meta">
      <div class="pill">
        <span class="dot" id="liveDot"></span>
        <span id="liveText">Otomatik güncelleniyor</span>
      </div>
      <div class="pill">
        <span>Kaynak:</span>
        <b id="source">—</b>
      </div>
      <div class="pill">
        <span>Son güncelleme:</span>
        <b id="lastUpdate">—</b>
      </div>
      <div class="pill">
        <span>Hayırlı işler dileriz</span>
      </div>
    </div>
  </div>

<script>
const PRODUCTS = {json.dumps(PRODUCTS, ensure_ascii=False)};

function trFormatInt(n) {{
  if (n === null || n === undefined) return "—";
  const s = n.toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ".");
  return s;
}}
function trFormatFloat(n) {{
  if (n === null || n === undefined) return "—";
  // 2 decimals
  const fixed = Number(n).toFixed(2);
  // 2123.45 -> 2.123,45
  let [a,b] = fixed.split(".");
  a = a.replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ".");
  return a + "," + b;
}}

function setClock() {{
  const d = new Date();
  const hh = String(d.getHours()).padStart(2,"0");
  const mm = String(d.getMinutes()).padStart(2,"0");
  document.getElementById("time").textContent = `${{hh}}:${{mm}}`;

  const months = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"];
  const days = ["Pazar","Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi"];
  const dateStr = `${{d.getDate()}} ${{months[d.getMonth()]}} ${{d.getFullYear()}} • ${{days[d.getDay()]}}`;
  document.getElementById("date").textContent = dateStr;
}}
setClock();
setInterval(setClock, 1000);

function cardTemplate(p) {{
  const isOns = p.type === "ons";
  if (!isOns) {{
    return `
      <div class="card" id="card_${{p.key}}">
        <div class="title">${{p.title}}</div>
        <div class="rows">
          <div class="row">
            <div class="lbl">Yeni</div>
            <div class="val" id="${{p.key}}_new_buy">— <small>₺</small></div>
            <div class="val" id="${{p.key}}_new_sell">— <small>₺</small></div>
          </div>
          <div class="row">
            <div class="lbl">Eski</div>
            <div class="val" id="${{p.key}}_old_buy">— <small>₺</small></div>
            <div class="val" id="${{p.key}}_old_sell">— <small>₺</small></div>
          </div>
        </div>
      </div>
    `;
  }} else {{
    return `
      <div class="card" id="card_${{p.key}}">
        <div class="title">${{p.title}} ALTIN</div>
        <div class="rows">
          <div class="row">
            <div class="lbl">Alış</div>
            <div class="val" id="${{p.key}}_buy">—</div>
            <div class="val" id="${{p.key}}_sell">—</div>
          </div>
        </div>
      </div>
    `;
  }}
}}

function buildGrid() {{
  const grid = document.getElementById("grid");
  grid.innerHTML = PRODUCTS.map(cardTemplate).join("");
}}
buildGrid();

function getPrev() {{
  try {{
    return JSON.parse(localStorage.getItem("prev_prices") || "{{}}");
  }} catch(e) {{
    return {{}};
  }}
}}
function setPrev(obj) {{
  try {{
    localStorage.setItem("prev_prices", JSON.stringify(obj));
  }} catch(e) {{}}
}}

function animateValue(el, direction) {{
  el.classList.remove("up","down");
  void el.offsetWidth; // restart animation
  el.classList.add(direction);

  // arrow add
  const existing = el.querySelector(".arrow");
  if (existing) existing.remove();
  const arrow = document.createElement("span");
  arrow.className = "arrow " + direction;
  arrow.textContent = direction === "up" ? "▲" : "▼";
  el.prepend(arrow);

  setTimeout(() => {{
    // clean arrow after some seconds
    const a = el.querySelector(".arrow");
    if (a) a.remove();
  }}, 1800);
}}

function updateField(fieldId, newVal, formatter, prevObj, prevKey) {{
  const el = document.getElementById(fieldId);
  if (!el) return;

  const prevVal = prevObj[prevKey];
  el.textContent = formatter(newVal);
  // add currency for TL fields where needed (they already include <small>₺</small>)
  // our formatter returns only number
  const hasSmall = el.querySelector("small");
  if (hasSmall) {{
    // rebuild with number + small
    const number = formatter(newVal);
    el.innerHTML = number + ' <small>₺</small>';
  }}

  if (prevVal === undefined || prevVal === null || newVal === null || newVal === undefined) return;

  if (typeof newVal === "number" && typeof prevVal === "number") {{
    if (newVal > prevVal) animateValue(el, "up");
    else if (newVal < prevVal) animateValue(el, "down");
  }}
}}

async function tick() {{
  try {{
    const r = await fetch("/prices", {{ cache: "no-store" }});
    const j = await r.json();

    const liveDot = document.getElementById("liveDot");
    const liveText = document.getElementById("liveText");
    const source = document.getElementById("source");
    const lastUpdate = document.getElementById("lastUpdate");

    source.textContent = j.source || "—";
    const dt = new Date((j.ts || Math.floor(Date.now()/1000)) * 1000);
    const hh = String(dt.getHours()).padStart(2,"0");
    const mm = String(dt.getMinutes()).padStart(2,"0");
    const ss = String(dt.getSeconds()).padStart(2,"0");
    lastUpdate.textContent = `${{hh}}:${{mm}}:${{ss}}`;

    if (j.ok) {{
      if (j.cache_used) {{
        liveDot.classList.add("bad");
        liveText.textContent = "Cache (geçici) kullanılıyor";
      }} else {{
        liveDot.classList.remove("bad");
        liveText.textContent = "Otomatik güncelleniyor";
      }}
    }} else {{
      liveDot.classList.add("bad");
      liveText.textContent = "Bağlantı sorunu";
    }}

    const data = j.data || {{}};
    const prev = getPrev();
    const nextPrev = {{}};

    for (const p of PRODUCTS) {{
      if (p.type === "sarrafiye") {{
        const item = data[p.key] || {{}};
        // Yeni
        updateField(`${{p.key}}_new_buy`, item.new_buy, trFormatInt, prev, `${{p.key}}.new_buy`);
        updateField(`${{p.key}}_new_sell`, item.new_sell, trFormatInt, prev, `${{p.key}}.new_sell`);
        // Eski
        updateField(`${{p.key}}_old_buy`, item.old_buy, trFormatInt, prev, `${{p.key}}.old_buy`);
        updateField(`${{p.key}}_old_sell`, item.old_sell, trFormatInt, prev, `${{p.key}}.old_sell`);

        nextPrev[`${{p.key}}.new_buy`] = item.new_buy;
        nextPrev[`${{p.key}}.new_sell`] = item.new_sell;
        nextPrev[`${{p.key}}.old_buy`] = item.old_buy;
        nextPrev[`${{p.key}}.old_sell`] = item.old_sell;
      }} else {{
        const item = data[p.key] || {{}};
        updateField(`${{p.key}}_buy`, item.buy, trFormatFloat, prev, `${{p.key}}.buy`);
        updateField(`${{p.key}}_sell`, item.sell, trFormatFloat, prev, `${{p.key}}.sell`);
        nextPrev[`${{p.key}}.buy`] = item.buy;
        nextPrev[`${{p.key}}.sell`] = item.sell;
      }}
    }}

    setPrev(nextPrev);
  }} catch (e) {{
    const liveDot = document.getElementById("liveDot");
    const liveText = document.getElementById("liveText");
    liveDot.classList.add("bad");
    liveText.textContent = "Bağlantı sorunu";
  }}
}}

tick();
setInterval(tick, {max(5000, CACHE_TTL_SECONDS * 1000)});
</script>
</body>
</html>
    """
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    # Local run:
    # python app.py
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)