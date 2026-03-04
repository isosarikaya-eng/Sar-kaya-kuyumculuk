from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse
import os
import time
import requests

app = FastAPI()

# --- AYARLAR ---
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "10"))   # TV için 10 sn güzel
OZBAG_API_URL = os.getenv("OZBAG_API_URL", "").strip()          # örn: https://.../ozbag
LOGO_URL = os.getenv("LOGO_URL", "").strip()                    # örn: /static/logo.png veya https://...

# STATIC (logo / css için)
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

_cache = {"ts": 0, "data": None}

def _safe_get_json(url: str, timeout: int = 10):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _compute_from_gram(gram_tl: float):
    """
    Basit hesap:
      Çeyrek = gram * 1.75
      Yarım  = gram * 3.50
      Tam    = gram * 7.00
    İstersen buraya darbe/işçilik ekleriz.
    """
    return {
        "ceyrek": int(round(gram_tl * 1.75)),
        "yarim":  int(round(gram_tl * 3.50)),
        "tam":    int(round(gram_tl * 7.00)),
    }

def _fetch_prices():
    # Cache
    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < CACHE_TTL_SECONDS:
        return _cache["data"]

    data = None

    # 1) Özbağ API varsa oradan çek
    if OZBAG_API_URL:
        try:
            data = _safe_get_json(OZBAG_API_URL, timeout=10)
        except Exception:
            data = None

    # 2) Özbağ yoksa örnek fallback (senin ilk test değerlerin)
    #    Burayı istersen gram altın çekip otomatik hesap yaptıracağız.
    if not data:
        data = {"gram_tl": 6942.0}  # örnek

    # Eğer gram_tl geldiyse otomatik çeyrek/yarım/tam hesapla
    if "gram_tl" in data and all(k not in data for k in ("ceyrek", "yarim", "tam")):
        calc = _compute_from_gram(float(data["gram_tl"]))
        data.update(calc)

    # cache'e yaz
    _cache["ts"] = now
    _cache["data"] = data
    return data

@app.get("/")
def health():
    return {"ok": True, "service": "sarikaya-tv", "cache_ttl_seconds": CACHE_TTL_SECONDS}

@app.get("/prices")
def prices():
    data = _fetch_prices()

    # TV için sadece 3 kalem döndürelim (küçük harf anahtar)
    payload = {
        "ceyrek": int(data.get("ceyrek", 0)),
        "yarim":  int(data.get("yarim", 0)),
        "tam":    int(data.get("tam", 0)),
        "updated_at": int(time.time())
    }

    # Türkçe karakter bozulmasın diye ensure_ascii=False
    return JSONResponse(content=payload, ensure_ascii=False)

@app.get("/prices.csv", response_class=PlainTextResponse)
def prices_csv():
    p = prices().body.decode("utf-8", errors="ignore")
    # p JSON string; pratik olsun diye yeniden üretelim
    data = _fetch_prices()
    csv = "kalem,fiyat\n"
    csv += f"Ceyrek,{int(data.get('ceyrek',0))}\n"
    csv += f"Yarim,{int(data.get('yarim',0))}\n"
    csv += f"Tam,{int(data.get('tam',0))}\n"
    return csv

@app.get("/tv", response_class=HTMLResponse)
def tv():
    logo = LOGO_URL or "/static/logo.png"  # logo.png koyarsan otomatik gelir

    # Yatay / profesyonel ekran: büyük font, hizalı kolon, otomatik refresh
    html = f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SARIKAYA KUYUMCULUK - TV</title>
  <style>
    :root {{
      --bg: #05070b;
      --card: rgba(255,255,255,0.06);
      --text: #ffffff;
      --muted: rgba(255,255,255,0.65);
      --accent: #d4af37; /* altın tonu */
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial;
      background: radial-gradient(1200px 600px at 20% 10%, rgba(212,175,55,0.14), transparent 55%),
                  radial-gradient(900px 500px at 80% 20%, rgba(255,255,255,0.08), transparent 55%),
                  var(--bg);
      color: var(--text);
      height: 100vh;
      overflow: hidden;
    }}
    .wrap {{
      height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr auto;
      padding: 28px 42px;
      gap: 18px;
    }}
    .top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 14px;
    }}
    .logo {{
      width: 56px;
      height: 56px;
      object-fit: contain;
      border-radius: 12px;
      background: rgba(255,255,255,0.06);
      padding: 10px;
    }}
    .title {{
      line-height: 1.05;
    }}
    .title .name {{
      font-weight: 800;
      letter-spacing: 1px;
      font-size: 22px;
    }}
    .title .sub {{
      margin-top: 4px;
      font-size: 14px;
      color: var(--muted);
      letter-spacing: 0.5px;
    }}
    .clock {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    .clock .t {{
      font-size: 18px;
      font-weight: 700;
    }}
    .clock .d {{
      font-size: 13px;
      color: var(--muted);
      margin-top: 2px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1.2fr 1fr 1fr;
      gap: 18px;
      height: 100%;
    }}
    .card {{
      background: var(--card);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 22px;
      padding: 28px 28px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      position: relative;
      overflow: hidden;
    }}
    .card:before {{
      content: "";
      position: absolute;
      inset: -40%;
      background: radial-gradient(circle at 30% 30%, rgba(212,175,55,0.18), transparent 55%);
      transform: rotate(12deg);
      pointer-events: none;
    }}
    .label {{
      position: relative;
      font-size: 22px;
      letter-spacing: 1px;
      color: var(--muted);
      margin-bottom: 12px;
      font-weight: 700;
    }}
    .price {{
      position: relative;
      font-size: 68px;
      font-weight: 900;
      letter-spacing: 1px;
      font-variant-numeric: tabular-nums;
      display: flex;
      align-items: baseline;
      gap: 12px;
    }}
    .price .cur {{
      font-size: 34px;
      color: var(--accent);
      font-weight: 900;
    }}
    .foot {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: var(--muted);
      font-size: 14px;
    }}
    .badge {{
      padding: 8px 12px;
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 999px;
      background: rgba(255,255,255,0.05);
    }}
    .pulse {{
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #3cff9a;
      margin-right: 8px;
      box-shadow: 0 0 0 0 rgba(60,255,154,0.6);
      animation: pulse 1.8s infinite;
      vertical-align: middle;
    }}
    @keyframes pulse {{
      0% {{ box-shadow: 0 0 0 0 rgba(60,255,154,0.55); }}
      70% {{ box-shadow: 0 0 0 14px rgba(60,255,154,0); }}
      100% {{ box-shadow: 0 0 0 0 rgba(60,255,154,0); }}
    }}
    .flash-up {{ animation: flashUp 0.5s ease; }}
    .flash-down {{ animation: flashDown 0.5s ease; }}
    @keyframes flashUp {{
      0% {{ transform: translateY(0); }}
      50% {{ transform: translateY(-2px); color: #3cff9a; }}
      100% {{ transform: translateY(0); }}
    }}
    @keyframes flashDown {{
      0% {{ transform: translateY(0); }}
      50% {{ transform: translateY(2px); color: #ff5c5c; }}
      100% {{ transform: translateY(0); }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <img class="logo" src="{logo}" onerror="this.style.display='none'" />
        <div class="title">
          <div class="name">SARIKAYA KUYUMCULUK</div>
          <div class="sub">Canlı Fiyat Ekranı</div>
        </div>
      </div>
      <div class="clock">
        <div class="t" id="clock"></div>
        <div class="d" id="date"></div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="label">ÇEYREK</div>
        <div class="price"><span id="ceyrek">-</span> <span class="cur">₺</span></div>
      </div>

      <div class="card">
        <div class="label">YARIM</div>
        <div class="price"><span id="yarim">-</span> <span class="cur">₺</span></div>
      </div>

      <div class="card">
        <div class="label">TAM</div>
        <div class="price"><span id="tam">-</span> <span class="cur">₺</span></div>
      </div>
    </div>

    <div class="foot">
      <div class="badge"><span class="pulse"></span>Otomatik Güncelleniyor</div>
      <div class="badge">Kaynak: Sistem</div>
      <div class="badge" id="last">Son güncelleme: -</div>
    </div>
  </div>

<script>
  const fmt = new Intl.NumberFormat('tr-TR');
  const ids = ["ceyrek","yarim","tam"];
  let last = {{ ceyrek: null, yarim: null, tam: null }};

  function setClock() {{
    const now = new Date();
    document.getElementById("clock").textContent =
      now.toLocaleTimeString('tr-TR', {{hour:'2-digit', minute:'2-digit'}});
    document.getElementById("date").textContent =
      now.toLocaleDateString('tr-TR', {{weekday:'long', year:'numeric', month:'long', day:'numeric'}});
  }}

  function flash(el, dir) {{
    el.classList.remove("flash-up","flash-down");
    void el.offsetWidth;
    el.classList.add(dir > 0 ? "flash-up" : "flash-down");
  }}

  async function load() {{
    try {{
      const r = await fetch('/prices', {{ cache: 'no-store' }});
      const j = await r.json();

      ids.forEach(k => {{
        const el = document.getElementById(k);
        const val = Number(j[k] || 0);
        el.textContent = fmt.format(val);

        if (last[k] !== null && val !== last[k]) {{
          flash(el, val > last[k] ? 1 : -1);
        }}
        last[k] = val;
      }});

      const ts = j.updated_at ? new Date(j.updated_at * 1000) : new Date();
      document.getElementById("last").textContent = "Son güncelleme: " + ts.toLocaleTimeString('tr-TR');
    }} catch (e) {{
      // sessiz geç
    }}
  }}

  setClock();
  setInterval(setClock, 1000 * 10);

  load();
  setInterval(load, {max(CACHE_TTL_SECONDS, 5)} * 1000);
</script>
</body>
</html>
"""
    return html