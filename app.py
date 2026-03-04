import os
import time
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# ======================
# CONFIG (ENV)
# ======================
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))  # 30 sn cache
OZBAG_API_URL = os.getenv("OZBAG_API_URL", "").strip()         # dış kaynak json endpoint
LOGO_URL = os.getenv("LOGO_URL", "").strip()                   # istersen https://.../logo.png

# Eğer projede static/ varsa mount et (yoksa crash etmez)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Basit in-memory cache
_cache: Dict[str, Any] = {"ts": 0.0, "data": None, "source": "YEDEK", "error": None}


# ======================
# HELPERS
# ======================
def _now_ts() -> float:
    return time.time()


def _safe_get_json(url: str, timeout: int = 8) -> Dict[str, Any]:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "sarıkaya-tv/1.0"})
    r.raise_for_status()
    # Bazı endpointler text/plain döndürebilir; JSON parse dene
    return r.json()


def _normalize_prices(payload: Dict[str, Any]) -> Tuple[Dict[str, int], str]:
    """
    Dış kaynak farklı format dönebilir.
    Beklenen final format:
      {"ceyrek": 12150, "yarim": 24300, "tam": 48600}

    Kabul edilen input örnekleri:
      - {"ceyrek":12150,"yarim":24300,"tam":48600}
      - {"Çeyrek":12150,"Yarım":24300,"Tam":48600}
      - {"gram_tl": 3450} -> basit çarpanla hesaplar
      - {"data": {...}} gibi nested
    """
    data = payload
    if "data" in payload and isinstance(payload["data"], dict):
        data = payload["data"]

    # 1) direkt coin fiyatları
    key_map = {
        "çeyrek": "ceyrek",
        "ceyrek": "ceyrek",
        "yarım": "yarim",
        "yarim": "yarim",
        "tam": "tam",
        "ziynet": "tam",
    }

    found: Dict[str, int] = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        kk = k.strip().lower()
        if kk in key_map:
            try:
                found[key_map[kk]] = int(round(float(v)))
            except Exception:
                pass

    if all(x in found for x in ("ceyrek", "yarim", "tam")):
        return found, "API"

    # 2) gram üzerinden hesap
    gram_keys = ["gram", "gram_tl", "gram_altin", "gram_altin_tl"]
    gram_val: Optional[float] = None
    for gk in gram_keys:
        if gk in data:
            try:
                gram_val = float(data[gk])
                break
            except Exception:
                pass

    if gram_val is not None:
        # Basit referans: çeyrek ~ 1.75g, yarım ~ 3.50g, tam ~ 7.00g
        c = int(round(gram_val * 1.75))
        y = int(round(gram_val * 3.50))
        t = int(round(gram_val * 7.00))
        return {"ceyrek": c, "yarim": y, "tam": t}, "API"

    # hiçbiri yoksa hata
    raise ValueError("Beklenen fiyat alanları bulunamadı.")


def _get_prices_cached() -> Dict[str, Any]:
    # cache taze ise dön
    if _cache["data"] is not None and (_now_ts() - float(_cache["ts"])) < CACHE_TTL_SECONDS:
        return _cache

    # Yedek (her zaman çalışır)
    fallback_prices = {"ceyrek": 12150, "yarim": 24300, "tam": 48600}
    result = {
        "ts": _now_ts(),
        "data": fallback_prices,
        "source": "YEDEK",
        "error": None,
    }

    if OZBAG_API_URL:
        try:
            payload = _safe_get_json(OZBAG_API_URL)
            normalized, src = _normalize_prices(payload)
            result["data"] = normalized
            result["source"] = src
        except Exception as e:
            result["error"] = f"API okunamadı: {e}"

    _cache.update(result)
    return _cache


def _logo_src() -> str:
    # Öncelik: LOGO_URL env → /static/logo.png → boş
    if LOGO_URL:
        return LOGO_URL
    if os.path.isdir("static") and os.path.exists("static/logo.png"):
        return "/static/logo.png"
    return ""


# ======================
# ROUTES
# ======================
@app.get("/", response_class=JSONResponse)
def health():
    return {
        "ok": True,
        "service": "sarıkaya-tv",
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "ozbag_api_url_set": bool(OZBAG_API_URL),
        "logo_url_set": bool(LOGO_URL),
    }


@app.get("/prices", response_class=JSONResponse)
def prices():
    c = _get_prices_cached()
    return {
        "ceyrek": c["data"]["ceyrek"],
        "yarim": c["data"]["yarim"],
        "tam": c["data"]["tam"],
        "source": c["source"],
        "last_update_ts": int(c["ts"]),
        "error": c["error"],
    }


@app.get("/tv", response_class=HTMLResponse)
def tv():
    logo = _logo_src()
    # Yatay / TV moduna uygun, otomatik yenileyen ekran
    html = f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Sarıkaya Kuyumculuk • Canlı Fiyat</title>
  <style>
    :root {{
      --bg1: #0b0c10;
      --bg2: #15161b;
      --gold: #d6b15d;
      --muted: rgba(255,255,255,.65);
      --card: rgba(255,255,255,.06);
      --stroke: rgba(214,177,93,.25);
      --ok: #35d07f;
      --bad: #ff4d4d;
    }}
    *{{box-sizing:border-box}}
    body {{
      margin:0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      color: #fff;
      background: radial-gradient(1200px 600px at 20% 0%, rgba(214,177,93,.12), transparent 60%),
                  radial-gradient(900px 500px at 80% 20%, rgba(255,255,255,.06), transparent 55%),
                  linear-gradient(180deg, var(--bg2), var(--bg1));
      height: 100vh;
      overflow:hidden;
    }}
    .wrap {{
      height:100vh;
      padding: 42px 56px;
      display:flex;
      flex-direction:column;
      gap: 26px;
    }}
    .top {{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap: 24px;
    }}
    .brand {{
      display:flex;
      gap: 18px;
      align-items:flex-start;
    }}
    .logo {{
      width: 70px;
      height: 70px;
      border-radius: 16px;
      background: rgba(255,255,255,.06);
      border: 1px solid rgba(255,255,255,.10);
      display:flex;
      align-items:center;
      justify-content:center;
      overflow:hidden;
    }}
    .logo img {{
      width:100%;
      height:100%;
      object-fit:cover;
    }}
    .brand h1 {{
      margin:0;
      font-size: 42px;
      letter-spacing: 2px;
      color: var(--gold);
    }}
    .brand .sub {{
      margin-top: 6px;
      font-size: 18px;
      color: var(--muted);
    }}
    .time {{
      text-align:right;
    }}
    .clock {{
      font-size: 56px;
      font-weight: 700;
      letter-spacing: 2px;
    }}
    .date {{
      margin-top: 6px;
      font-size: 20px;
      color: var(--muted);
      line-height: 1.2;
    }}
    .cards {{
      flex:1;
      display:grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 22px;
      align-items:stretch;
    }}
    .card {{
      border-radius: 36px;
      background: linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03));
      border: 1px solid var(--stroke);
      box-shadow: 0 20px 60px rgba(0,0,0,.35);
      padding: 34px 34px 28px 34px;
      display:flex;
      flex-direction:column;
      justify-content:space-between;
      min-height: 420px;
    }}
    .label {{
      font-size: 26px;
      letter-spacing: 2px;
      color: rgba(255,255,255,.75);
      font-weight: 700;
    }}
    .price {{
      font-size: 92px;
      font-weight: 800;
      letter-spacing: 1px;
      display:flex;
      align-items:baseline;
      gap: 14px;
      margin-top: 24px;
    }}
    .tl {{
      font-size: 64px;
      color: var(--gold);
      font-weight: 800;
    }}
    .footer {{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap: 18px;
      padding-top: 8px;
      color: var(--muted);
      font-size: 18px;
    }}
    .pill {{
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,.10);
      padding: 12px 16px;
      background: rgba(0,0,0,.22);
      display:flex;
      align-items:center;
      gap: 10px;
    }}
    .dot {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: var(--bad);
      box-shadow: 0 0 18px rgba(255,77,77,.35);
    }}
    .dot.ok {{
      background: var(--ok);
      box-shadow: 0 0 18px rgba(53,208,127,.35);
    }}
    @media (max-width: 1100px) {{
      .wrap{{padding: 28px 22px}}
      .brand h1{{font-size: 30px}}
      .clock{{font-size: 44px}}
      .cards{{grid-template-columns: 1fr;}}
      .price{{font-size: 76px}}
      .tl{{font-size: 52px}}
      .card{{min-height: 240px}}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo">
          {f'<img src="{logo}" alt="logo"/>' if logo else '<span style="color:rgba(255,255,255,.35);font-weight:700">SK</span>'}
        </div>
        <div>
          <h1>SARIKAYA KUYUMCULUK</h1>
          <div class="sub">Canlı Fiyat Ekranı</div>
        </div>
      </div>
      <div class="time">
        <div class="clock" id="clock">--:--</div>
        <div class="date" id="date">--</div>
      </div>
    </div>

    <div class="cards">
      <div class="card">
        <div class="label">ÇEYREK</div>
        <div class="price"><span id="ceyrek">—</span><span class="tl">₺</span></div>
      </div>
      <div class="card">
        <div class="label">YARIM</div>
        <div class="price"><span id="yarim">—</span><span class="tl">₺</span></div>
      </div>
      <div class="card">
        <div class="label">TAM</div>
        <div class="price"><span id="tam">—</span><span class="tl">₺</span></div>
      </div>
    </div>

    <div class="footer">
      <div class="pill">
        <span class="dot" id="dot"></span>
        <span id="status">YEDEK</span>
        <span>•</span>
        <span id="src">Kaynak: -</span>
      </div>
      <div class="pill">
        <span id="updated">Son güncelleme: -</span>
        <span>•</span>
        <span>Hayırlı işler dileriz</span>
      </div>
    </div>
  </div>

<script>
  function pad(n){{ return String(n).padStart(2,'0'); }}
  function fmt(n){{
    try {{
      return Number(n).toLocaleString('tr-TR');
    }} catch(e) {{
      return String(n);
    }}
  }}

  function tickClock(){{
    const d = new Date();
    document.getElementById('clock').textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
    const opts = {{ weekday:'long', year:'numeric', month:'long', day:'numeric' }};
    document.getElementById('date').textContent = d.toLocaleDateString('tr-TR', opts);
  }}
  setInterval(tickClock, 1000);
  tickClock();

  async function refreshPrices(){{
    try {{
      const r = await fetch('/prices?ts=' + Date.now(), {{ cache: 'no-store' }});
      if(!r.ok) throw new Error('HTTP ' + r.status);
      const j = await r.json();

      document.getElementById('ceyrek').textContent = fmt(j.ceyrek);
      document.getElementById('yarim').textContent  = fmt(j.yarim);
      document.getElementById('tam').textContent    = fmt(j.tam);

      const ok = (j.source && j.source !== 'YEDEK' && !j.error);
      const dot = document.getElementById('dot');
      dot.classList.toggle('ok', ok);

      document.getElementById('status').textContent = ok ? 'OTOMATİK' : 'YEDEK';
      document.getElementById('src').textContent = 'Kaynak: ' + (ok ? 'Özbağ / API' : ('Yedek (API yok/erişilemiyor)'));
      const when = j.last_update_ts ? new Date(j.last_update_ts * 1000) : new Date();
      document.getElementById('updated').textContent = 'Son güncelleme: ' + when.toLocaleTimeString('tr-TR');

    }} catch (e) {{
      // Ekran boş kalmasın, en azından status güncellensin
      document.getElementById('dot').classList.remove('ok');
      document.getElementById('status').textContent = 'YEDEK';
      document.getElementById('src').textContent = 'Kaynak: Yedek (hata)';
    }}
  }}

  refreshPrices();
  setInterval(refreshPrices, 10000); // 10 sn
</script>
</body>
</html>
"""
    return HTMLResponse(html)