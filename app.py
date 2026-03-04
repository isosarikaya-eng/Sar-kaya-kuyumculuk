import os
import time
import json
from typing import Any, Dict, Tuple, Optional

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# =========================
# FASTAPI APP
# =========================
app = FastAPI(title="Sarıkaya Kuyumculuk TV", version="3.0.0")

# CORS (istersen kapatabilirsin; TV ekranı için genelde sorun olmaz)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# =========================
# ENV / SETTINGS
# =========================
# Railway: PORT environment variable usually exists.
PORT = int(os.getenv("PORT", "8000"))  # (uvicorn config uses it, but harmless here)

# Your upstream API URL (Özbağ veya kendi endpoint'in)
OZBAG_API_URL = os.getenv("OZBAG_API_URL", "").strip()

# Optional: If your upstream requires header/token
# Example: "Authorization: Bearer xxx" or "x-api-key: xxx"
OZBAG_API_TOKEN = os.getenv("OZBAG_API_TOKEN", "").strip()

# Logo for TV header (public URL to png/svg/jpg). If empty -> SK badge fallback.
LOGO_URL = os.getenv("LOGO_URL", "").strip()

# Cache TTL (seconds)
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))

# Poll interval suggestion for frontend (ms)
FRONTEND_REFRESH_MS = int(os.getenv("FRONTEND_REFRESH_MS", "8000"))

# Request timeouts / retries
HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "4.0"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "2"))

# Fallback prices (safe defaults)
FALLBACK_PRICES = {
    "ceyrek": int(os.getenv("FALLBACK_CEYREK", "12150")),
    "yarim": int(os.getenv("FALLBACK_YARIM", "24300")),
    "tam": int(os.getenv("FALLBACK_TAM", "48600")),
}

# =========================
# SIMPLE IN-MEMORY CACHE
# =========================
_cache: Dict[str, Any] = {
    "ts": 0.0,
    "data": None,     # type: Optional[Dict[str, Any]]
    "source": "YEDEK",
    "error": None,
}

# =========================
# HELPERS
# =========================
def _now() -> float:
    return time.time()

def _is_cache_valid() -> bool:
    if not _cache["data"]:
        return False
    return (_now() - float(_cache["ts"])) < float(CACHE_TTL_SECONDS)

def _safe_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        # allow "12.150" or "12,150" style
        if isinstance(x, str):
            s = x.strip().replace(".", "").replace(",", "")
            if s == "":
                return None
            return int(s)
        if isinstance(x, (int, float)):
            return int(round(float(x)))
        return None
    except Exception:
        return None

def _normalize_payload(payload: Any) -> Optional[Dict[str, int]]:
    """
    Accepts multiple shapes and tries to extract:
    ceyrek, yarim, tam
    """
    if not isinstance(payload, dict):
        return None

    # common possibilities:
    # 1) {"ceyrek":12150,"yarim":24300,"tam":48600}
    # 2) {"data":{"ceyrek":...}}
    # 3) {"result":{...}}
    candidates = []
    candidates.append(payload)
    for k in ["data", "result", "prices", "price", "payload"]:
        if isinstance(payload.get(k), dict):
            candidates.append(payload.get(k))

    for obj in candidates:
        c = _safe_int(obj.get("ceyrek") or obj.get("çeyrek") or obj.get("CEYREK"))
        y = _safe_int(obj.get("yarim") or obj.get("yarım") or obj.get("YARIM"))
        t = _safe_int(obj.get("tam") or obj.get("TAM"))

        if c and y and t:
            return {"ceyrek": c, "yarim": y, "tam": t}

    return None

def _requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "SarıkayaTV/3.0 (+railway)"
    })
    # Token header optional
    if OZBAG_API_TOKEN:
        # You can change header name if your API needs something else
        s.headers.update({"Authorization": OZBAG_API_TOKEN})
    return s

def _fetch_from_api() -> Tuple[Optional[Dict[str, int]], Optional[str]]:
    """
    Returns (prices, error_message)
    prices is dict if success else None
    """
    if not OZBAG_API_URL:
        return None, "OZBAG_API_URL boş"

    s = _requests_session()
    last_err = None

    for attempt in range(1, HTTP_RETRIES + 2):  # e.g. retries=2 => total 3 tries
        try:
            r = s.get(OZBAG_API_URL, timeout=HTTP_TIMEOUT_SECONDS)
            # Handle non-200 gracefully
            if r.status_code < 200 or r.status_code >= 300:
                last_err = f"API HTTP {r.status_code}"
                continue

            # Some APIs return text/html by mistake; try json safely
            try:
                payload = r.json()
            except Exception:
                # try parse if it's plain text JSON
                try:
                    payload = json.loads(r.text)
                except Exception:
                    last_err = "API JSON parse hatası"
                    continue

            prices = _normalize_payload(payload)
            if not prices:
                last_err = "API format uyumsuz"
                continue

            return prices, None

        except Exception as e:
            last_err = f"API istek hatası: {type(e).__name__}"
            continue

    return None, last_err or "API bilinmeyen hata"

def get_prices() -> Tuple[Dict[str, int], str, Optional[str]]:
    """
    Always returns prices dict.
    source: API / CACHE / YEDEK
    error: string if fallback happened
    """
    if _is_cache_valid():
        return _cache["data"], "CACHE", _cache.get("error")

    prices, err = _fetch_from_api()

    if prices:
        _cache["data"] = prices
        _cache["ts"] = _now()
        _cache["source"] = "API"
        _cache["error"] = None
        return prices, "API", None

    # fallback
    _cache["data"] = dict(FALLBACK_PRICES)
    _cache["ts"] = _now()
    _cache["source"] = "YEDEK"
    _cache["error"] = err
    return dict(FALLBACK_PRICES), "YEDEK", err

def _fmt_tr(n: int) -> str:
    # Turkish thousand separator with dot
    s = f"{n:,}".replace(",", ".")
    return s

# =========================
# ROUTES
# =========================
@app.get("/health")
def health():
    # Never fails
    return {
        "ok": True,
        "has_api_url": bool(OZBAG_API_URL),
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "frontend_refresh_ms": FRONTEND_REFRESH_MS,
        "time": int(_now()),
    }

@app.get("/prices")
def prices():
    p, source, err = get_prices()
    # Never 500: always returns a valid JSON
    return JSONResponse({
        "ceyrek": int(p["ceyrek"]),
        "yarim": int(p["yarim"]),
        "tam": int(p["tam"]),
        "source": source,
        "error": err,           # null if ok
        "ts": int(_now()),
        "cache_ttl": CACHE_TTL_SECONDS
    })

@app.get("/tv", response_class=HTMLResponse)
def tv():
    # Single-page, landscape TV optimized
    logo_html = ""
    if LOGO_URL:
        # show provided logo
        logo_html = f'<img class="logo" src="{LOGO_URL}" alt="Logo" onerror="this.style.display=\'none\';document.getElementById(\'fallbackBadge\').style.display=\'flex\';" />'
    # fallback badge always exists
    badge_html = '<div id="fallbackBadge" class="badge" style="display:none;">SK</div>'
    if not LOGO_URL:
        badge_html = '<div id="fallbackBadge" class="badge" style="display:flex;">SK</div>'

    html = f"""
<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Sarıkaya Kuyumculuk • TV</title>
<style>
:root {{
  --bg1:#07070a;
  --bg2:#0e0f16;
  --card:#141622cc;
  --stroke:rgba(212,175,55,.28);
  --gold:#d4af37;
  --text:#f2f2f2;
  --muted:rgba(255,255,255,.65);
  --muted2:rgba(255,255,255,.45);
  --danger:#ff4d4d;
  --ok:#1fe38a;
}}

*{{box-sizing:border-box}}
html,body{{height:100%;margin:0;background:radial-gradient(1200px 800px at 30% 10%, #1b1b22 0%, var(--bg1) 45%, var(--bg2) 100%);color:var(--text);font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial;}}
.wrap{{height:100%;display:flex;flex-direction:column;padding:36px 48px;gap:22px}}
.header{{display:flex;align-items:center;justify-content:space-between;gap:18px}}
.brand{{display:flex;align-items:center;gap:16px;min-width:0}}
.logo{{width:64px;height:64px;object-fit:contain;border-radius:14px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);padding:10px}}
.badge{{width:64px;height:64px;border-radius:14px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);display:flex;align-items:center;justify-content:center;font-weight:800;letter-spacing:.12em;color:var(--gold)}}
.titles{{display:flex;flex-direction:column;min-width:0}}
.h1{{font-size:44px;line-height:1.05;letter-spacing:.08em;font-weight:900;color:var(--gold);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.sub{{margin-top:6px;font-size:18px;color:var(--muted)}}
.clock{{text-align:right}}
.time{{font-size:60px;font-weight:900;letter-spacing:.02em}}
.date{{margin-top:6px;font-size:18px;color:var(--muted)}}

.grid{{flex:1;display:grid;grid-template-columns:repeat(3, 1fr);gap:22px;align-items:stretch}}
.card{{border-radius:26px;background:linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.02));border:1px solid var(--stroke);box-shadow:0 22px 60px rgba(0,0,0,.55);padding:26px 26px 18px 26px;display:flex;flex-direction:column;justify-content:space-between;min-height:0}}
.label{{font-size:22px;letter-spacing:.16em;font-weight:800;color:rgba(255,255,255,.70)}}
.valueRow{{display:flex;align-items:baseline;justify-content:flex-start;gap:14px;min-width:0}}
.value{{font-size:88px;font-weight:950;letter-spacing:.02em;white-space:nowrap}}
.tl{{font-size:56px;font-weight:900;color:var(--gold)}}
.small{{font-size:16px;color:var(--muted2)}}

.footer{{display:flex;align-items:center;justify-content:space-between;gap:16px}}
.pill{{border-radius:999px;padding:12px 16px;border:1px solid rgba(255,255,255,.10);background:rgba(0,0,0,.25);display:flex;align-items:center;gap:10px;color:var(--muted)}}
.dot{{width:10px;height:10px;border-radius:999px;background:var(--danger)}}
.dot.ok{{background:var(--ok)}}
.rightPills{{display:flex;gap:14px;flex-wrap:wrap;justify-content:flex-end}}
.kayan{{opacity:.9}}

@media (max-width: 1100px){{
  .wrap{{padding:18px}}
  .h1{{font-size:34px}}
  .time{{font-size:46px}}
  .value{{font-size:66px}}
  .tl{{font-size:44px}}
}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="brand">
      {logo_html}
      {badge_html}
      <div class="titles">
        <div class="h1">SARIKAYA KUYUMCULUK</div>
        <div class="sub">Canlı Fiyat Ekranı • TV</div>
      </div>
    </div>
    <div class="clock">
      <div class="time" id="clockTime">--:--</div>
      <div class="date" id="clockDate">--</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="label">ÇEYREK</div>
      <div class="valueRow">
        <div class="value" id="ceyrekVal">--</div>
        <div class="tl">₺</div>
      </div>
      <div class="small">Güncelleme: <span id="ceyrekTs">-</span></div>
    </div>

    <div class="card">
      <div class="label">YARIM</div>
      <div class="valueRow">
        <div class="value" id="yarimVal">--</div>
        <div class="tl">₺</div>
      </div>
      <div class="small">Güncelleme: <span id="yarimTs">-</span></div>
    </div>

    <div class="card">
      <div class="label">TAM</div>
      <div class="valueRow">
        <div class="value" id="tamVal">--</div>
        <div class="tl">₺</div>
      </div>
      <div class="small">Güncelleme: <span id="tamTs">-</span></div>
    </div>
  </div>

  <div class="footer">
    <div class="pill" id="statusPill">
      <span class="dot" id="statusDot"></span>
      <span id="statusText">Bağlanıyor…</span>
    </div>
    <div class="rightPills">
      <div class="pill">Kaynak: <b id="srcText">-</b></div>
      <div class="pill">Son güncelleme: <b id="lastUpdate">-</b></div>
      <div class="pill kayan">Hayırlı işler dileriz</div>
    </div>
  </div>
</div>

<script>
const REFRESH_MS = {FRONTEND_REFRESH_MS};

function pad(n){{ return String(n).padStart(2,'0'); }}

function trThousands(n) {{
  // n is number
  try {{
    return n.toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ".");
  }} catch(e) {{
    return n;
  }}
}}

function setClock() {{
  const d = new Date();
  const hh = pad(d.getHours());
  const mm = pad(d.getMinutes());
  document.getElementById("clockTime").innerText = hh + ":" + mm;

  const days = ["Pazar","Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi"];
  const months = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"];
  const dateText = d.getDate() + " " + months[d.getMonth()] + " " + d.getFullYear() + " • " + days[d.getDay()];
  document.getElementById("clockDate").innerText = dateText;
}}
setClock();
setInterval(setClock, 1000);

async function loadPrices() {{
  try {{
    const r = await fetch("/prices", {{ cache: "no-store" }});
    const data = await r.json();

    document.getElementById("ceyrekVal").innerText = trThousands(data.ceyrek);
    document.getElementById("yarimVal").innerText  = trThousands(data.yarim);
    document.getElementById("tamVal").innerText    = trThousands(data.tam);

    const now = new Date();
    const ts = pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
    document.getElementById("ceyrekTs").innerText = ts;
    document.getElementById("yarimTs").innerText  = ts;
    document.getElementById("tamTs").innerText    = ts;

    document.getElementById("srcText").innerText = data.source || "-";
    document.getElementById("lastUpdate").innerText = ts;

    const pill = document.getElementById("statusPill");
    const dot  = document.getElementById("statusDot");
    const text = document.getElementById("statusText");

    if ((data.source || "") === "API" || (data.source || "") === "CACHE") {{
      dot.classList.add("ok");
      text.innerText = "Otomatik güncelleniyor";
    }} else {{
      dot.classList.remove("ok");
      text.innerText = "YEDEK • API yok/erişilemiyor";
    }}

  }} catch(e) {{
    // UI never breaks
    const dot  = document.getElementById("statusDot");
    const text = document.getElementById("statusText");
    dot.classList.remove("ok");
    text.innerText = "Bağlantı sorunu";
  }}
}}

loadPrices();
setInterval(loadPrices, REFRESH_MS);
</script>

</body>
</html>
"""
    return HTMLResponse(html)

# Root redirect / helpful info
@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(
        """
        <html><body style="font-family:Arial;padding:24px">
        <h2>Sarıkaya Kuyumculuk TV</h2>
        <ul>
          <li><a href="/tv">/tv</a> (TV ekranı)</li>
          <li><a href="/prices">/prices</a> (JSON fiyat)</li>
          <li><a href="/health">/health</a> (sağlık)</li>
        </ul>
        </body></html>
        """
    )