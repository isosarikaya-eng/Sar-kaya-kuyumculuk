import os
import time
import logging
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("sarıkaya-tv")

# ----------------------------
# Config (ENV)
# ----------------------------
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))
OZBAG_API_URL = os.getenv("OZBAG_API_URL", "").strip()
LOGO_URL = os.getenv("LOGO_URL", "/static/logo.png").strip()  # can be /static/logo.png or full https URL

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "6.0"))  # seconds
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "10"))     # TV refresh interval

# ----------------------------
# FastAPI
# ----------------------------
app = FastAPI(title="Sarıkaya Kuyumculuk TV", version="2.0.0")

# ----------------------------
# Static (NEVER CRASH if missing)
# ----------------------------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    log.info("Static mounted: %s", STATIC_DIR)
else:
    # Older crash cause: Directory 'static' does not exist
    log.warning("Static dir missing, skipping mount: %s", STATIC_DIR)

# ----------------------------
# In-memory cache
# ----------------------------
_cache: Dict[str, Any] = {
    "ts": 0.0,
    "data": None,
    "source": "YEDEK",
    "last_ok": None,  # last successful API fetch time
    "last_error": None,
}

# ----------------------------
# Backup prices (used when API fails)
# ----------------------------
BACKUP_PRICES = {
    "ceyrek": 12150,
    "yarim": 24300,
    "tam": 48600,
}

# ----------------------------
# Helpers
# ----------------------------
def _now_ts() -> float:
    return time.time()

def _is_cache_valid() -> bool:
    if _cache["data"] is None:
        return False
    return (_now_ts() - float(_cache["ts"])) < CACHE_TTL_SECONDS

def _thousands_tr(n: int) -> str:
    # 12150 -> 12.150
    s = f"{n:,}".replace(",", ".")
    return s

def _self_url_from_request(request: Request) -> str:
    # e.g. https://web-production-xxx.up.railway.app
    return str(request.base_url).rstrip("/")

def _looks_self_referential(url: str, request: Request) -> bool:
    # Prevent setting OZBAG_API_URL to our own /ozbag or /prices which can loop or fail
    if not url:
        return False
    base = _self_url_from_request(request)
    return url.startswith(base)

def _safe_get_json(url: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json(), None
    except Exception as e:
        return None, str(e)

def _extract_prices_from_payload(payload: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """
    Tries to understand different possible API schemas.
    Accepts either:
      - direct: {"ceyrek": 12345, "yarim": 24690, "tam": 49380}
      - nested: {"data": {...}}
      - gram-based: {"gram_tl": 3500} etc -> we compute coins
    """
    def _dig(d: Dict[str, Any], keys) -> Any:
        cur = d
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur

    # 1) try direct coin prices
    candidates = [
        payload,
        payload.get("data") if isinstance(payload.get("data"), dict) else None,
        payload.get("result") if isinstance(payload.get("result"), dict) else None,
    ]
    candidates = [c for c in candidates if isinstance(c, dict)]

    for c in candidates:
        # common key variants
        ceyrek = c.get("ceyrek") or c.get("quarter") or c.get("ceyrek_tl")
        yarim  = c.get("yarim")  or c.get("half")    or c.get("yarim_tl")
        tam    = c.get("tam")    or c.get("full")    or c.get("tam_tl")

        if all(isinstance(x, (int, float, str)) for x in [ceyrek, yarim, tam]):
            try:
                return {
                    "ceyrek": int(float(ceyrek)),
                    "yarim": int(float(yarim)),
                    "tam": int(float(tam)),
                }
            except Exception:
                pass

    # 2) try gram gold -> compute
    gram_keys = [
        ("gram_tl",),
        ("gram",),
        ("gram_altin",),
        ("data", "gram_tl"),
        ("data", "gram"),
    ]
    gram_val = None
    for keys in gram_keys:
        v = _dig(payload, keys) if len(keys) > 1 else payload.get(keys[0])
        if isinstance(v, (int, float, str)):
            try:
                gram_val = float(v)
                break
            except Exception:
                pass

    if gram_val is not None and gram_val > 0:
        # Basit hesap (senin eski mantık): Çeyrek=gram*1.75, Yarım=gram*3.5, Tam=gram*7
        return {
            "ceyrek": int(round(gram_val * 1.75)),
            "yarim": int(round(gram_val * 3.50)),
            "tam": int(round(gram_val * 7.00)),
        }

    return None

def _get_prices(request: Request) -> Dict[str, Any]:
    """
    Returns dict:
    {
      prices: {ceyrek:int, yarim:int, tam:int},
      source: "API" | "CACHE" | "YEDEK",
      last_update_ts: float,
      last_ok_ts: float|None,
      error: str|None
    }
    """
    # Cache valid
    if _is_cache_valid():
        return {
            "prices": _cache["data"],
            "source": "CACHE",
            "last_update_ts": _cache["ts"],
            "last_ok_ts": _cache["last_ok"],
            "error": None,
        }

    # If URL missing or self-referential -> fallback
    if not OZBAG_API_URL:
        _cache["data"] = BACKUP_PRICES
        _cache["source"] = "YEDEK"
        _cache["ts"] = _now_ts()
        _cache["last_error"] = "OZBAG_API_URL not set"
        return {
            "prices": BACKUP_PRICES,
            "source": "YEDEK",
            "last_update_ts": _cache["ts"],
            "last_ok_ts": _cache["last_ok"],
            "error": _cache["last_error"],
        }

    if _looks_self_referential(OZBAG_API_URL, request):
        # This is the common mistake: setting OZBAG_API_URL to this app's URL (loop)
        _cache["data"] = BACKUP_PRICES
        _cache["source"] = "YEDEK"
        _cache["ts"] = _now_ts()
        _cache["last_error"] = "OZBAG_API_URL points to this app (self-referential). Use real Ozbag endpoint."
        log.error(_cache["last_error"])
        return {
            "prices": BACKUP_PRICES,
            "source": "YEDEK",
            "last_update_ts": _cache["ts"],
            "last_ok_ts": _cache["last_ok"],
            "error": _cache["last_error"],
        }

    payload, err = _safe_get_json(OZBAG_API_URL)
    if payload is None:
        _cache["data"] = BACKUP_PRICES
        _cache["source"] = "YEDEK"
        _cache["ts"] = _now_ts()
        _cache["last_error"] = f"API fetch failed: {err}"
        log.warning(_cache["last_error"])
        return {
            "prices": BACKUP_PRICES,
            "source": "YEDEK",
            "last_update_ts": _cache["ts"],
            "last_ok_ts": _cache["last_ok"],
            "error": _cache["last_error"],
        }

    prices = _extract_prices_from_payload(payload)
    if prices is None:
        _cache["data"] = BACKUP_PRICES
        _cache["source"] = "YEDEK"
        _cache["ts"] = _now_ts()
        _cache["last_error"] = "API payload recognized but schema not understood"
        log.warning(_cache["last_error"])
        return {
            "prices": BACKUP_PRICES,
            "source": "YEDEK",
            "last_update_ts": _cache["ts"],
            "last_ok_ts": _cache["last_ok"],
            "error": _cache["last_error"],
        }

    # OK
    _cache["data"] = prices
    _cache["source"] = "API"
    _cache["ts"] = _now_ts()
    _cache["last_ok"] = _cache["ts"]
    _cache["last_error"] = None
    log.info("API OK: %s", prices)

    return {
        "prices": prices,
        "source": "API",
        "last_update_ts": _cache["ts"],
        "last_ok_ts": _cache["last_ok"],
        "error": None,
    }

# ----------------------------
# Routes
# ----------------------------
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/tv")

@app.get("/health", response_class=JSONResponse)
def health():
    return {
        "status": "ok",
        "version": app.version,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "ozbag_api_url_set": bool(OZBAG_API_URL),
        "static_dir_exists": os.path.isdir(STATIC_DIR),
    }

@app.get("/prices", response_class=JSONResponse)
def prices(request: Request):
    data = _get_prices(request)
    return data

@app.get("/ozbag", response_class=JSONResponse)
def ozbag_proxy(request: Request):
    """
    Optional: This endpoint returns the RAW Ozbag payload (for debugging).
    If OZBAG_API_URL is not set or invalid, returns error.
    """
    if not OZBAG_API_URL:
        return JSONResponse({"ok": False, "error": "OZBAG_API_URL not set"}, status_code=400)

    if _looks_self_referential(OZBAG_API_URL, request):
        return JSONResponse(
            {"ok": False, "error": "OZBAG_API_URL points to this app. Use real Ozbag endpoint."},
            status_code=400,
        )

    payload, err = _safe_get_json(OZBAG_API_URL)
    if payload is None:
        return JSONResponse({"ok": False, "error": f"fetch failed: {err}"}, status_code=502)
    return {"ok": True, "payload": payload}

@app.get("/tv", response_class=HTMLResponse)
def tv(request: Request):
    # Server-side initial load
    data = _get_prices(request)
    p = data["prices"]

    # LOGO_URL can be /static/logo.png OR full http(s)
    logo_src = LOGO_URL or "/static/logo.png"

    # Build HTML (no template engine needed)
    html = f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sarıkaya Kuyumculuk • TV</title>
  <style>
    :root {{
      --bg1: #0b0c10;
      --bg2: #12131a;
      --gold: #c9a24a;
      --card: rgba(255,255,255,0.06);
      --card2: rgba(255,255,255,0.03);
      --line: rgba(201,162,74,0.35);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.65);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, system-ui, Segoe UI, Roboto, Arial, sans-serif;
      color: var(--text);
      background: radial-gradient(1200px 600px at 20% 0%, rgba(201,162,74,0.18), transparent 55%),
                  radial-gradient(900px 500px at 90% 20%, rgba(201,162,74,0.10), transparent 60%),
                  linear-gradient(180deg, var(--bg1), var(--bg2));
      height: 100vh;
      overflow: hidden;
    }}
    .wrap {{
      height: 100vh;
      padding: 28px 36px;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 18px;
    }}
    .top {{
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: start;
      gap: 18px;
    }}
    .brand {{
      display:flex;
      align-items:center;
      gap: 14px;
    }}
    .logo {{
      width: 54px;
      height: 54px;
      border-radius: 14px;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.10);
      display:flex;
      align-items:center;
      justify-content:center;
      overflow:hidden;
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
      font-weight: 800;
      letter-spacing: 0.5px;
      color: var(--gold);
      font-size: 44px;
    }}
    .title .sub {{
      margin-top: 6px;
      font-size: 16px;
      color: var(--muted);
    }}
    .clock {{
      text-align:right;
      line-height:1.05;
    }}
    .clock .time {{
      font-size: 64px;
      font-weight: 800;
    }}
    .clock .date {{
      margin-top: 6px;
      font-size: 18px;
      color: var(--muted);
    }}

    /* cards row */
    .cards {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
      align-items: stretch;
      min-height: 0;
    }}
    .card {{
      border-radius: 28px;
      background: linear-gradient(180deg, var(--card), var(--card2));
      border: 1px solid rgba(255,255,255,0.10);
      box-shadow: 0 20px 40px rgba(0,0,0,0.35);
      position: relative;
      overflow: hidden;
      padding: 26px;
      display:flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .card:before {{
      content:"";
      position:absolute;
      inset:0;
      border: 1px solid var(--line);
      border-radius: 28px;
      pointer-events:none;
    }}
    .label {{
      font-size: 22px;
      letter-spacing: 3px;
      color: rgba(255,255,255,0.72);
      font-weight: 700;
    }}
    .value {{
      display:flex;
      align-items: baseline;
      gap: 14px;
      margin-top: 8px;
    }}
    .num {{
      font-size: 96px;
      font-weight: 900;
      letter-spacing: 0.5px;
      white-space: nowrap;
    }}
    .try {{
      font-size: 62px;
      font-weight: 900;
      color: var(--gold);
    }}
    .updated {{
      font-size: 18px;
      color: rgba(255,255,255,0.55);
    }}

    .bottom {{
      display:flex;
      gap: 14px;
      align-items:center;
      justify-content: space-between;
      flex-wrap: wrap;
    }}
    .pill {{
      border-radius: 999px;
      padding: 12px 16px;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.10);
      color: rgba(255,255,255,0.78);
      display:flex;
      align-items:center;
      gap: 10px;
      font-size: 16px;
    }}
    .dot {{
      width: 10px; height: 10px;
      border-radius: 99px;
      background: #21c55d;
    }}
    .dot.red {{ background:#ef4444; }}
    .muted {{ color: rgba(255,255,255,0.62); }}

    /* Make it strongly landscape-friendly */
    @media (max-width: 1000px) {{
      .title h1 {{ font-size: 34px; }}
      .clock .time {{ font-size: 52px; }}
      .num {{ font-size: 72px; }}
      .try {{ font-size: 48px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo" title="Sarıkaya Kuyumculuk">
          <img src="{logo_src}" onerror="this.style.display='none'; this.parentElement.textContent='SK';" alt="logo">
        </div>
        <div class="title">
          <h1>SARIKAYA KUYUMCULUK</h1>
          <div class="sub">Canlı Fiyat Ekranı • TV</div>
        </div>
      </div>
      <div class="clock">
        <div class="time" id="clockTime">--:--</div>
        <div class="date" id="clockDate">--</div>
      </div>
    </div>

    <div class="cards">
      <div class="card">
        <div class="label">ÇEYREK</div>
        <div class="value">
          <div class="num" id="ceyrekNum">{_thousands_tr(int(p["ceyrek"]))}</div>
          <div class="try">₺</div>
        </div>
        <div class="updated">Güncelleme: <span id="ceyrekTs">--</span></div>
      </div>

      <div class="card">
        <div class="label">YARIM</div>
        <div class="value">
          <div class="num" id="yarimNum">{_thousands_tr(int(p["yarim"]))}</div>
          <div class="try">₺</div>
        </div>
        <div class="updated">Güncelleme: <span id="yarimTs">--</span></div>
      </div>

      <div class="card">
        <div class="label">TAM</div>
        <div class="value">
          <div class="num" id="tamNum">{_thousands_tr(int(p["tam"]))}</div>
          <div class="try">₺</div>
        </div>
        <div class="updated">Güncelleme: <span id="tamTs">--</span></div>
      </div>
    </div>

    <div class="bottom">
      <div class="pill" id="pillStatus">
        <span class="dot" id="dotStatus"></span>
        <span id="statusText">Otomatik güncelleniyor</span>
      </div>

      <div class="pill">
        <span class="muted">Kaynak:</span>
        <b id="sourceText">{data["source"]}</b>
      </div>

      <div class="pill">
        <span class="muted">Son güncelleme:</span>
        <b id="lastUpdate">--</b>
      </div>

      <div class="pill">
        Hayırlı işler dileriz
      </div>
    </div>
  </div>

<script>
  const REFRESH_SECONDS = {REFRESH_SECONDS};

  function pad(n) {{ return String(n).padStart(2, '0'); }}
  function fmtTime(d) {{ return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds()); }}
  function fmtDateTR(d) {{
    const days = ["Pazar","Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi"];
    const months = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"];
    return d.getDate() + " " + months[d.getMonth()] + " " + d.getFullYear() + " • " + days[d.getDay()];
  }}

  function thousandsTR(x) {{
    const s = String(x);
    return s.replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ".");
  }}

  function updateClock() {{
    const d = new Date();
    document.getElementById("clockTime").textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
    document.getElementById("clockDate").textContent = fmtDateTR(d);
  }}

  async function refreshPrices() {{
    try {{
      const res = await fetch("/prices", {{ cache: "no-store" }});
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();

      const p = data.prices;
      const ts = new Date((data.last_update_ts || Date.now()/1000) * 1000);
      const stamp = fmtTime(ts);

      document.getElementById("ceyrekNum").textContent = thousandsTR(p.ceyrek);
      document.getElementById("yarimNum").textContent  = thousandsTR(p.yarim);
      document.getElementById("tamNum").textContent    = thousandsTR(p.tam);

      document.getElementById("ceyrekTs").textContent = stamp;
      document.getElementById("yarimTs").textContent  = stamp;
      document.getElementById("tamTs").textContent    = stamp;

      document.getElementById("sourceText").textContent = data.source || "YEDEK";
      document.getElementById("lastUpdate").textContent = stamp;

      // status pill
      const dot = document.getElementById("dotStatus");
      const st = document.getElementById("statusText");
      if ((data.source || "") === "API") {{
        dot.className = "dot";
        st.textContent = "Otomatik güncelleniyor";
      }} else if ((data.source || "") === "CACHE") {{
        dot.className = "dot";
        st.textContent = "Cache (geçici) kullanılıyor";
      }} else {{
        dot.className = "dot red";
        st.textContent = "YEDEK • API yok/erişilemiyor";
      }}
    }} catch (e) {{
      const dot = document.getElementById("dotStatus");
      const st = document.getElementById("statusText");
      dot.className = "dot red";
      st.textContent = "Bağlantı sorunu";
    }}
  }}

  updateClock();
  setInterval(updateClock, 1000);
  refreshPrices();
  setInterval(refreshPrices, REFRESH_SECONDS * 1000);
</script>
</body>
</html>
"""
    return HTMLResponse(html)