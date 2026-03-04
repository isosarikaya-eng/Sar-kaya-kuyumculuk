from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import os
import time
import threading
import requests
from datetime import datetime

app = FastAPI()

# =========================
# Ayarlar (ENV)
# =========================
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))
OZBAG_API_URL = os.getenv("OZBAG_API_URL", "").strip()   # Özbağ GERÇEK API URL'si olmalı
LOGO_URL = os.getenv("LOGO_URL", "").strip()             # https://.../logo.png veya /static/logo.png

# Eğer repo'da static klasörü varsa mount et (yoksa ASLA crash olmaz)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# =========================
# Dahili cache + kilit
# =========================
_lock = threading.Lock()
_cache = {
    "ts": 0.0,
    "data": None,          # {"ceyrek":..., "yarim":..., "tam":...}
    "source": "YEDEK",     # API / CACHE / YEDEK
    "last_error": None,
}

# Yedek fiyatlar (en kötü senaryoda bile ekranda boş kalmasın)
FALLBACK_PRICES = {"ceyrek": 12150, "yarim": 24300, "tam": 48600}


def _now_tr():
    # Türkiye saati +0300 için basit format (Railway container UTC olabilir)
    # İstersen TZ env ile netleştiririz; şimdilik frontend kendi saatini gösteriyor.
    return datetime.now().strftime("%H:%M:%S")


def _fmt_try(n: int) -> str:
    # 48600 -> 48.600
    s = f"{int(n):,}".replace(",", ".")
    return s


def _safe_get_json(url: str, timeout: int = 8):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "SarıkayaTV/1.0"})
    r.raise_for_status()
    return r.json()


def _normalize_prices(payload) -> dict | None:
    """
    Özbağ'dan dönen JSON formatı farklı olabilir.
    Biz şunları kabul ediyoruz:
    1) {"ceyrek":12150,"yarim":24300,"tam":48600}
    2) {"data":{"ceyrek":...}} gibi nested
    3) Büyük/küçük harf farkları
    """
    if payload is None:
        return None

    # nested "data" varsa içeri gir
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
        payload = payload["data"]

    if not isinstance(payload, dict):
        return None

    # anahtarları normalize et
    def pick(*keys):
        for k in keys:
            if k in payload:
                return payload[k]
        return None

    c = pick("ceyrek", "Çeyrek", "CEYREK", "quarter")
    y = pick("yarim", "Yarım", "YARIM", "half")
    t = pick("tam", "Tam", "TAM", "full")

    try:
        if c is None or y is None or t is None:
            return None
        return {"ceyrek": int(float(c)), "yarim": int(float(y)), "tam": int(float(t))}
    except Exception:
        return None


def _is_self_url(url: str) -> bool:
    # OZBAG_API_URL yanlışlıkla kendi domainine verilmişse döngüyü kır
    base = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    # Railway bazen bunu vermez; yine de heuristik:
    # url içinde "railway.app" + bizim own path "/ozbag" ise riskli.
    if not url:
        return False
    if "/ozbag" in url and "railway.app" in url:
        # self olma ihtimali yüksek; %100 değil ama seni korur.
        return True
    # Ek kontrol: HOST header ile karşılaştırma yapamıyoruz burada.
    return False


def _fetch_prices_from_api() -> tuple[dict | None, str | None]:
    """
    Başarılıysa (prices, None), değilse (None, error_text)
    """
    if not OZBAG_API_URL:
        return None, "OZBAG_API_URL tanımlı değil"

    if _is_self_url(OZBAG_API_URL):
        return None, "OZBAG_API_URL kendi uygulamana verilmiş (döngü riski). Gerçek Özbağ API URL girilmeli."

    try:
        payload = _safe_get_json(OZBAG_API_URL, timeout=8)
        prices = _normalize_prices(payload)
        if not prices:
            return None, f"API JSON formatı beklenen değil: {str(payload)[:120]}"
        return prices, None
    except Exception as e:
        return None, f"API hata: {type(e).__name__}: {e}"


def get_prices(force_refresh: bool = False) -> dict:
    """
    Dışarıya tek tip döner.
    """
    now = time.time()
    with _lock:
        age = now - _cache["ts"]
        if (not force_refresh) and _cache["data"] and age < CACHE_TTL_SECONDS:
            return {
                "ok": True,
                "source": "CACHE",
                "age_seconds": int(age),
                "prices": _cache["data"],
                "updated_at": _cache["ts"],
                "last_error": _cache["last_error"],
            }

    prices, err = _fetch_prices_from_api()
    with _lock:
        if prices:
            _cache["ts"] = now
            _cache["data"] = prices
            _cache["source"] = "API"
            _cache["last_error"] = None
            return {
                "ok": True,
                "source": "API",
                "age_seconds": 0,
                "prices": prices,
                "updated_at": now,
                "last_error": None,
            }

        # API yoksa yedeğe düş (cache varsa onu göster)
        if _cache["data"]:
            # Cache var ama API çöktü
            _cache["last_error"] = err
            return {
                "ok": True,
                "source": "CACHE",
                "age_seconds": int(now - _cache["ts"]),
                "prices": _cache["data"],
                "updated_at": _cache["ts"],
                "last_error": err,
            }

        # Hiç veri yoksa fallback
        _cache["ts"] = now
        _cache["data"] = FALLBACK_PRICES
        _cache["source"] = "YEDEK"
        _cache["last_error"] = err
        return {
            "ok": False,
            "source": "YEDEK",
            "age_seconds": 0,
            "prices": FALLBACK_PRICES,
            "updated_at": now,
            "last_error": err,
        }


# =========================
# Routes
# =========================
@app.get("/health")
def health():
    # hızlı teşhis için
    return {
        "status": "ok",
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "ozbag_api_url_set": bool(OZBAG_API_URL),
        "logo_url": LOGO_URL or None,
        "static_dir_exists": os.path.isdir("static"),
    }


@app.get("/prices")
def prices():
    data = get_prices(force_refresh=False)
    # 500 yerine her zaman JSON dönelim
    return JSONResponse(data)


@app.get("/refresh")
def refresh():
    data = get_prices(force_refresh=True)
    return JSONResponse(data)


@app.get("/tv", response_class=HTMLResponse)
def tv():
    # LOGO: boşsa basit "SK" rozet göster
    logo_html = ""
    if LOGO_URL:
        logo_html = f'<img class="logo" src="{LOGO_URL}" alt="logo" onerror="this.style.display=\'none\';document.getElementById(\'logoFallback\').style.display=\'flex\';">'
    logo_fallback = '<div id="logoFallback" class="logoFallback">SK</div>'

    html = f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sarıkaya Kuyumculuk - TV</title>
  <style>
    :root {{
      --bg: #0b0b0f;
      --card: rgba(255,255,255,0.06);
      --stroke: rgba(212,175,55,0.35);
      --gold: #d4af37;
      --text: #f3f4f6;
      --muted: rgba(243,244,246,0.65);
      --danger: #ff3b30;
      --ok: #34c759;
    }}
    html, body {{ height: 100%; }}
    body {{
      margin: 0;
      background: radial-gradient(1200px 600px at 20% 10%, rgba(212,175,55,0.12), transparent 60%),
                  radial-gradient(900px 500px at 80% 30%, rgba(255,255,255,0.06), transparent 55%),
                  var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    }}
    .wrap {{
      height: 100%;
      display: flex;
      flex-direction: column;
      padding: 28px 34px;
      box-sizing: border-box;
      gap: 18px;
    }}
    .top {{
      display: grid;
      grid-template-columns: 96px 1fr 320px;
      gap: 18px;
      align-items: center;
    }}
    .logoBox {{
      width: 86px; height: 86px;
      border-radius: 18px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.10);
      display: flex; align-items: center; justify-content: center;
      overflow: hidden;
      position: relative;
    }}
    .logo {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
    .logoFallback {{
      width: 100%; height: 100%;
      display: none;
      align-items: center; justify-content: center;
      font-weight: 800;
      color: var(--gold);
      letter-spacing: 1px;
    }}
    .brand h1 {{
      margin: 0;
      font-size: 44px;
      letter-spacing: 1px;
      color: var(--gold);
      font-weight: 800;
    }}
    .brand .sub {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 18px;
    }}
    .clock {{
      text-align: right;
    }}
    .clock .time {{
      font-size: 64px;
      font-weight: 800;
      line-height: 1;
    }}
    .clock .date {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 18px;
    }}

    .cards {{
      flex: 1;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
      min-height: 0;
    }}
    .card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03));
      border: 1px solid var(--stroke);
      border-radius: 28px;
      padding: 22px 26px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      box-shadow: 0 18px 60px rgba(0,0,0,0.45);
    }}
    .label {{
      font-size: 26px;
      letter-spacing: 2px;
      color: rgba(243,244,246,0.75);
      font-weight: 800;
    }}
    .value {{
      display: flex;
      align-items: baseline;
      gap: 14px;
      margin-top: 8px;
    }}
    .num {{
      font-size: 110px;
      font-weight: 900;
      letter-spacing: 1px;
      line-height: 1;
    }}
    .try {{
      font-size: 62px;
      color: var(--gold);
      font-weight: 900;
    }}
    .updated {{
      color: rgba(243,244,246,0.45);
      font-size: 18px;
    }}

    .bottom {{
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 14px;
      align-items: center;
    }}
    .pill {{
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 999px;
      padding: 14px 18px;
      color: rgba(243,244,246,0.72);
      display: flex;
      gap: 12px;
      align-items: center;
      justify-content: center;
      font-size: 18px;
    }}
    .dot {{
      width: 10px; height: 10px; border-radius: 999px;
      background: var(--ok);
      box-shadow: 0 0 0 4px rgba(52,199,89,0.15);
    }}
    .dot.red {{
      background: var(--danger);
      box-shadow: 0 0 0 4px rgba(255,59,48,0.15);
    }}
    .rightNote {{
      justify-content: flex-end;
    }}

    @media (max-width: 1100px) {{
      .top {{ grid-template-columns: 96px 1fr; }}
      .clock {{ text-align: left; }}
      .cards {{ grid-template-columns: 1fr; }}
      .num {{ font-size: 86px; }}
      .try {{ font-size: 50px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="logoBox">
        {logo_html}
        {logo_fallback}
      </div>

      <div class="brand">
        <h1>SARIKAYA KUYUMCULUK</h1>
        <div class="sub">Canlı Fiyat Ekranı • TV</div>
      </div>

      <div class="clock">
        <div class="time" id="t_time">--:--</div>
        <div class="date" id="t_date">--</div>
      </div>
    </div>

    <div class="cards">
      <div class="card">
        <div class="label">ÇEYREK</div>
        <div class="value">
          <div class="num" id="v_ceyrek">--</div>
          <div class="try">₺</div>
        </div>
        <div class="updated">Güncelleme: <span id="u_ceyrek">--</span></div>
      </div>

      <div class="card">
        <div class="label">YARIM</div>
        <div class="value">
          <div class="num" id="v_yarim">--</div>
          <div class="try">₺</div>
        </div>
        <div class="updated">Güncelleme: <span id="u_yarim">--</span></div>
      </div>

      <div class="card">
        <div class="label">TAM</div>
        <div class="value">
          <div class="num" id="v_tam">--</div>
          <div class="try">₺</div>
        </div>
        <div class="updated">Güncelleme: <span id="u_tam">--</span></div>
      </div>
    </div>

    <div class="bottom">
      <div class="pill" id="p_auto"><span class="dot" id="dot"></span>Otomatik güncelleniyor</div>
      <div class="pill" id="p_source">Kaynak: --</div>
      <div class="pill rightNote" id="p_last">Son güncelleme: --</div>
    </div>
  </div>

<script>
  function pad(n) {{ return String(n).padStart(2,'0'); }}

  function tickClock() {{
    const d = new Date();
    document.getElementById('t_time').textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
    const days = ["Pazar","Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi"];
    const months = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"];
    document.getElementById('t_date').textContent =
      d.getDate() + " " + months[d.getMonth()] + " " + d.getFullYear() + " • " + days[d.getDay()];
  }}
  setInterval(tickClock, 1000);
  tickClock();

  function fmtTR(n) {{
    try {{
      return new Intl.NumberFormat('tr-TR').format(Number(n));
    }} catch(e) {{
      return String(n);
    }}
  }}

  async function refresh() {{
    try {{
      const r = await fetch('/prices', {{ cache: 'no-store' }});
      const j = await r.json();

      const p = (j && j.prices) ? j.prices : null;
      if (p) {{
        document.getElementById('v_ceyrek').textContent = fmtTR(p.ceyrek);
        document.getElementById('v_yarim').textContent  = fmtTR(p.yarim);
        document.getElementById('v_tam').textContent    = fmtTR(p.tam);

        const now = new Date();
        const stamp = pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
        document.getElementById('u_ceyrek').textContent = stamp;
        document.getElementById('u_yarim').textContent  = stamp;
        document.getElementById('u_tam').textContent    = stamp;

        document.getElementById('p_last').textContent = "Son güncelleme: " + stamp;
      }}

      const source = j.source || "--";
      document.getElementById('p_source').textContent = "Kaynak: " + source;

      const dot = document.getElementById('dot');
      if (source === "API" || source === "CACHE") {{
        dot.classList.remove('red');
      }} else {{
        dot.classList.add('red');
      }}

      // hata varsa konsola yaz (TV ekranında bozmayalım)
      if (j.last_error) console.warn("last_error:", j.last_error);

    }} catch (e) {{
      console.error(e);
    }}
  }}

  // 5 sn'de bir güncelle
  setInterval(refresh, 5000);
  refresh();
</script>
</body>
</html>
"""
    return HTMLResponse(html)