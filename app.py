from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import os
import time
import requests

app = FastAPI()

# =========================
# CONFIG
# =========================
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "20"))  # TV için 20 sn iyi
OZBAG_API_URL = os.getenv("OZBAG_API_URL", "").strip()         # örn: https://....../prices
LOGO_URL = os.getenv("LOGO_URL", "").strip()                   # örn: /static/logo.png veya https://...

# Eğer static klasörü yoksa crash olmasın
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

_cache = {"ts": 0.0, "data": None, "ok": False, "error": ""}

FALLBACK = {
    "ceyrek": 12150,
    "yarim": 24300,
    "tam": 48600,
}

# =========================
# HELPERS
# =========================
def _now() -> float:
    return time.time()

def _safe_get_json(url: str, timeout: int = 8):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "sarıkaya-tv/1.0"})
        r.raise_for_status()
        return r.json(), ""
    except Exception as e:
        return None, str(e)

def _normalize_prices(data: dict):
    """
    Dış API'den gelen farklı key formatlarını normalize eder.
    Kabul edilen olası keyler:
      ceyrek / Çeyrek / Ceyrek
      yarim  / Yarım  / Yarim
      tam    / Tam
    """
    if not isinstance(data, dict):
        return None

    # key normalize
    def pick(*keys):
        for k in keys:
            if k in data:
                return data[k]
        return None

    c = pick("ceyrek", "Çeyrek", "Ceyrek", "CEYREK", "çeyrek")
    y = pick("yarim", "Yarım", "Yarim", "YARIM", "yarım")
    t = pick("tam", "Tam", "TAM")

    # bazen string gelebilir
    try:
        c = int(float(c)) if c is not None else None
        y = int(float(y)) if y is not None else None
        t = int(float(t)) if t is not None else None
    except Exception:
        return None

    if c and y and t:
        return {"ceyrek": c, "yarim": y, "tam": t}
    return None

def _get_prices():
    """
    Cache + dış kaynak + fallback
    Asla exception fırlatmaz.
    """
    age = _now() - _cache["ts"]
    if _cache["data"] is not None and age < CACHE_TTL_SECONDS:
        return _cache["data"], _cache["ok"], _cache["error"]

    # varsayılan: fallback
    out = dict(FALLBACK)
    ok = False
    err = ""

    if OZBAG_API_URL:
        data, err = _safe_get_json(OZBAG_API_URL)
        norm = _normalize_prices(data) if data else None
        if norm:
            out = norm
            ok = True
            err = ""
        else:
            ok = False
            err = err or "API yanıtı beklenen formatta değil."

    _cache["ts"] = _now()
    _cache["data"] = out
    _cache["ok"] = ok
    _cache["error"] = err
    return out, ok, err

def _fmt_try(v):
    try:
        return f"{int(v):,}".replace(",", ".")
    except Exception:
        return "-"

# =========================
# ROUTES
# =========================
@app.get("/")
def health():
    return {
        "ok": True,
        "service": "sarikaya-tv",
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "ozbag_api_set": bool(OZBAG_API_URL),
        "logo_set": bool(LOGO_URL),
    }

@app.get("/prices")
def prices():
    data, ok, err = _get_prices()
    # Asla 500 dönmesin
    return JSONResponse(
        status_code=200,
        content={
            "source_ok": ok,
            "error": err,
            "data": data
        }
    )

@app.get("/prices.csv", response_class=PlainTextResponse)
def prices_csv():
    data, ok, err = _get_prices()
    # Google Sheets IMPORTDATA için sade CSV
    # (Sheets hata vermesin diye 200 dönüyoruz)
    csv = (
        "kalem,fiyat\n"
        f"ceyrek,{data.get('ceyrek','')}\n"
        f"yarim,{data.get('yarim','')}\n"
        f"tam,{data.get('tam','')}\n"
    )
    return csv

@app.get("/tv", response_class=HTMLResponse)
def tv():
    data, ok, err = _get_prices()

    c = _fmt_try(data.get("ceyrek"))
    y = _fmt_try(data.get("yarim"))
    t = _fmt_try(data.get("tam"))

    logo_html = ""
    if LOGO_URL:
        logo_html = f'<img class="logo" src="{LOGO_URL}" alt="logo" onerror="this.style.display=\'none\'" />'

    status_text = "CANLI" if ok else "YEDEK"
    status_class = "ok" if ok else "bad"
    status_detail = "Kaynak: API" if ok else "Kaynak: Yedek (API yok/erişilemiyor)"

    # TV landscape tasarım (16:9)
    html = f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1"/>
  <meta http-equiv="refresh" content="{CACHE_TTL_SECONDS}">
  <title>Sarıkaya Kuyumculuk - TV</title>
  <style>
    :root {{
      --bg1:#07070a; --bg2:#0e0f16;
      --gold:#d7b46a; --gold2:#b9923b;
      --card: rgba(255,255,255,.06);
      --stroke: rgba(215,180,106,.25);
      --text:#ffffff;
      --muted: rgba(255,255,255,.65);
      --ok:#2dd36f;
      --bad:#ff453a;
    }}
    *{{box-sizing:border-box}}
    body {{
      margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial;
      color:var(--text);
      background: radial-gradient(1200px 600px at 20% 10%, rgba(215,180,106,.18), transparent 60%),
                  radial-gradient(900px 500px at 80% 20%, rgba(215,180,106,.10), transparent 55%),
                  linear-gradient(120deg, var(--bg1), var(--bg2));
      height:100vh; overflow:hidden;
    }}
    .wrap {{
      height:100vh; padding:34px 46px;
      display:flex; flex-direction:column; gap:22px;
    }}
    .top {{
      display:flex; align-items:center; justify-content:space-between;
    }}
    .brand {{
      display:flex; align-items:center; gap:18px;
    }}
    .logo {{
      width:64px; height:64px; border-radius:16px;
      background:rgba(255,255,255,.04);
      border:1px solid var(--stroke);
      padding:10px; object-fit:contain;
    }}
    .title {{
      display:flex; flex-direction:column; gap:6px;
    }}
    .title h1 {{
      margin:0; font-size:40px; letter-spacing:1px;
      color:var(--gold);
      text-transform:uppercase;
    }}
    .title .sub {{
      color:var(--muted); font-size:18px;
    }}
    .clock {{
      text-align:right;
    }}
    .clock .time {{
      font-size:42px; font-weight:700;
    }}
    .clock .date {{
      color:var(--muted); font-size:18px;
      margin-top:6px;
    }}
    .grid {{
      flex:1;
      display:grid;
      grid-template-columns: repeat(3, 1fr);
      gap:22px;
      align-items:stretch;
    }}
    .card {{
      background: linear-gradient(180deg, rgba(255,255,255,.08), rgba(255,255,255,.04));
      border:1px solid var(--stroke);
      border-radius:28px;
      padding:26px 30px;
      position:relative;
      overflow:hidden;
    }}
    .card:before {{
      content:"";
      position:absolute; inset:-40%;
      background: radial-gradient(circle at 30% 20%, rgba(215,180,106,.18), transparent 55%);
      transform:rotate(10deg);
    }}
    .card * {{ position:relative; }}
    .label {{
      font-size:28px; letter-spacing:2px;
      color:rgba(255,255,255,.8);
      text-transform:uppercase;
    }}
    .price {{
      margin-top:18px;
      display:flex; align-items:baseline; gap:14px;
      font-size:92px; font-weight:800;
    }}
    .cur {{
      font-size:54px;
      color:var(--gold);
      font-weight:800;
    }}
    .foot {{
      display:flex; align-items:center; justify-content:space-between;
      gap:18px;
      margin-top:4px;
    }}
    .pill {{
      display:inline-flex; align-items:center; gap:10px;
      padding:10px 14px;
      border-radius:999px;
      border:1px solid var(--stroke);
      background:rgba(0,0,0,.25);
      color:var(--muted);
      font-size:14px;
      white-space:nowrap;
    }}
    .dot {{
      width:10px; height:10px; border-radius:50%;
      background: var(--ok);
    }}
    .dot.bad {{ background: var(--bad); }}
    .status {{
      font-weight:700;
      color:#fff;
    }}
    .status.ok {{ color: var(--ok); }}
    .status.bad {{ color: var(--bad); }}
    .error {{
      max-width:46vw;
      overflow:hidden;
      text-overflow:ellipsis;
      white-space:nowrap;
      color:rgba(255,255,255,.45);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        {logo_html}
        <div class="title">
          <h1>SARIKAYA KUYUMCULUK</h1>
          <div class="sub">Canlı Fiyat Ekranı</div>
        </div>
      </div>
      <div class="clock">
        <div class="time" id="clock">--:--</div>
        <div class="date" id="date">--</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="label">ÇEYREK</div>
        <div class="price"><span>{c}</span><span class="cur">₺</span></div>
      </div>
      <div class="card">
        <div class="label">YARIM</div>
        <div class="price"><span>{y}</span><span class="cur">₺</span></div>
      </div>
      <div class="card">
        <div class="label">TAM</div>
        <div class="price"><span>{t}</span><span class="cur">₺</span></div>
      </div>
    </div>

    <div class="foot">
      <div class="pill">
        <span class="dot {'bad' if not ok else ''}"></span>
        <span class="status {status_class}">{status_text}</span>
        <span>•</span>
        <span>{status_detail}</span>
      </div>
      <div class="pill">
        <span>Otomatik yenileme:</span>
        <b>{CACHE_TTL_SECONDS}s</b>
      </div>
      <div class="pill error" title="{err}">
        {("Bağlantı sorunu: " + err) if (not ok and err) else "Hayırlı işler dileriz."}
      </div>
    </div>
  </div>

<script>
  function pad(n){{return String(n).padStart(2,'0');}}
  const days = ["Pazar","Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi"];
  const months = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"];
  function tick(){{
    const d = new Date();
    document.getElementById("clock").textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
    document.getElementById("date").textContent =
      d.getDate() + " " + months[d.getMonth()] + " " + d.getFullYear() + " • " + days[d.getDay()];
  }}
  tick(); setInterval(tick, 1000);
</script>
</body>
</html>
"""
    return HTMLResponse(content=html)

# Eski endpoint ile uyum (istersen kullan)
@app.get("/ozbag")
def ozbag_passthrough():
    data, ok, err = _get_prices()
    return {"ok": ok, "error": err, "data": data}