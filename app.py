from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import os
import time
import requests

app = FastAPI()

# --- AYARLAR ---
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))  # 30 sn iyi
OZBAG_API_URL = os.getenv("OZBAG_API_URL", "").strip()         # örn: https://.... (aşağıda anlatıyorum)
LOGO_URL = os.getenv("LOGO_URL", "").strip()                   # boşsa /static/logo.png kullanır

# STATIC (logo buradan)
app.mount("/static", StaticFiles(directory="static"), name="static")

_cache = {"ts": 0, "data": None}


def _safe_get_json(url: str, timeout: int = 10):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "sarıkaya-tv/1.0"})
    r.raise_for_status()
    return r.json()


def _compute_from_gram(gram_tl: float):
    """
    Basit hesap: Çeyrek=gram*1.75, Yarım=gram*3.5, Tam=gram*7.0
    (İstersen 'darbe/işçilik' farkını da parametre yaparız.)
    """
    ceyrek = round(gram_tl * 1.75)
    yarim = round(gram_tl * 3.50)
    tam = round(gram_tl * 7.00)
    return {"ceyrek": ceyrek, "yarim": yarim, "tam": tam}


def get_prices_auto():
    """
    1) OZBAG_API_URL varsa: oradan gram / çeyrek / yarım / tam al
    2) Yoksa: fallback sabit örnek (senin mevcut değerlerin)
    """
    # Fallback
    fallback = {"ceyrek": 12150, "yarim": 24300, "tam": 48600}

    if not OZBAG_API_URL:
        return fallback

    try:
        data = _safe_get_json(OZBAG_API_URL)

        # Beklenen olası formatlar:
        # A) {"gram": 2985, "ceyrek": 12150, "yarim": 24300, "tam": 48600}
        # B) {"gram": 2985} -> buradan hesaplarız
        # C) {"data": {"gram": ...}} gibi -> gerekirse uyarlarsın (bana atarsan 10 sn'de uyarlarım)

        if "gram" in data:
            gram = float(data["gram"])
            if all(k in data for k in ("ceyrek", "yarim", "tam")):
                return {
                    "ceyrek": int(data["ceyrek"]),
                    "yarim": int(data["yarim"]),
                    "tam": int(data["tam"]),
                    "gram": gram,
                }
            computed = _compute_from_gram(gram)
            computed["gram"] = gram
            return computed

        # Eğer direkt coin dönüyorsa:
        if all(k in data for k in ("ceyrek", "yarim", "tam")):
            return {
                "ceyrek": int(data["ceyrek"]),
                "yarim": int(data["yarim"]),
                "tam": int(data["tam"]),
            }

        return fallback

    except Exception:
        return fallback


def get_cached_prices():
    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"] < CACHE_TTL_SECONDS):
        return _cache["data"], _cache["ts"]

    prices = get_prices_auto()
    _cache["data"] = prices
    _cache["ts"] = now
    return prices, _cache["ts"]


@app.get("/", response_class=JSONResponse)
def health():
    return JSONResponse(
        {"ok": True, "service": "sarikaya-tv", "cache_ttl_seconds": CACHE_TTL_SECONDS},
        ensure_ascii=False
    )


@app.get("/prices", response_class=JSONResponse)
def prices():
    data, ts = get_cached_prices()
    payload = {
        "ceyrek": int(data["ceyrek"]),
        "yarim": int(data["yarim"]),
        "tam": int(data["tam"]),
        "gram": float(data["gram"]) if "gram" in data else None,
        "updated_at_unix": int(ts),
    }
    return JSONResponse(payload, ensure_ascii=False)


@app.get("/prices.csv", response_class=PlainTextResponse)
def prices_csv():
    data, ts = get_cached_prices()
    csv = (
        "kalem,fiyat\n"
        f"ceyrek,{int(data['ceyrek'])}\n"
        f"yarim,{int(data['yarim'])}\n"
        f"tam,{int(data['tam'])}\n"
    )
    return PlainTextResponse(csv, media_type="text/plain; charset=utf-8")


@app.get("/tv", response_class=HTMLResponse)
def tv():
    logo = LOGO_URL if LOGO_URL else "/static/logo.png"
    html = f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Sarıkaya Kuyumculuk - TV</title>
  <style>
    :root {{
      --bg: #0b0b0d;
      --gold: #d6b35a;
      --muted: #a7a7aa;
      --white: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(1200px 600px at 30% 20%, #1a1a1f, var(--bg));
      color: var(--white);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      overflow: hidden;
    }}
    .wrap {{
      height: 100vh;
      width: 100vw;
      padding: 4vh 5vw;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 2vh;
    }}
    .top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 18px;
    }}
    .brand img {{
      height: 64px;
      width: auto;
      object-fit: contain;
      filter: drop-shadow(0 8px 18px rgba(0,0,0,.35));
    }}
    .brand .title {{
      display: flex;
      flex-direction: column;
      line-height: 1.05;
    }}
    .brand .title .name {{
      font-weight: 800;
      letter-spacing: .08em;
      color: var(--gold);
      text-transform: uppercase;
      font-size: 26px;
    }}
    .brand .title .sub {{
      color: var(--muted);
      font-size: 15px;
      margin-top: 6px;
    }}
    .clock {{
      text-align: right;
      color: var(--muted);
      font-size: 18px;
    }}
    .panel {{
      border: 1px solid rgba(214,179,90,.25);
      border-radius: 18px;
      padding: 4vh 4vw;
      background: rgba(10,10,12,.55);
      box-shadow: 0 24px 60px rgba(0,0,0,.35);
      display: grid;
      grid-template-columns: 1fr;
      align-content: center;
      gap: 4vh;
    }}
    .row {{
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: baseline;
      gap: 2vw;
      padding: 2.2vh 0;
      border-bottom: 1px solid rgba(255,255,255,.06);
    }}
    .row:last-child {{ border-bottom: none; }}
    .label {{
      font-size: 56px;
      font-weight: 800;
      letter-spacing: .02em;
    }}
    .price {{
      font-size: 70px;
      font-weight: 900;
      color: var(--gold);
      letter-spacing: .01em;
    }}
    .price small {{
      font-size: 46px;
      margin-left: 10px;
      color: rgba(214,179,90,.95);
    }}
    .footer {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: var(--muted);
      font-size: 16px;
      letter-spacing: .02em;
    }}
    .badge {{
      padding: 10px 14px;
      border: 1px solid rgba(214,179,90,.25);
      border-radius: 999px;
      background: rgba(214,179,90,.08);
      color: rgba(214,179,90,.95);
    }}
    .marquee {{
      white-space: nowrap;
      overflow: hidden;
      flex: 1;
      margin-left: 16px;
    }}
    .marquee span {{
      display: inline-block;
      padding-left: 100%;
      animation: scroll 18s linear infinite;
    }}
    @keyframes scroll {{
      0% {{ transform: translateX(0); }}
      100% {{ transform: translateX(-100%); }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <img src="{logo}" alt="Sarıkaya Kuyumculuk"/>
        <div class="title">
          <div class="name">SARIKAYA KUYUMCULUK</div>
          <div class="sub">Fiyatlar anlıktır • TV ekranı</div>
        </div>
      </div>
      <div class="clock" id="clock">--:--</div>
    </div>

    <div class="panel">
      <div class="row">
        <div class="label">Çeyrek</div>
        <div class="price"><span id="ceyrek">--</span><small>₺</small></div>
      </div>
      <div class="row">
        <div class="label">Yarım</div>
        <div class="price"><span id="yarim">--</span><small>₺</small></div>
      </div>
      <div class="row">
        <div class="label">Tam</div>
        <div class="price"><span id="tam">--</span><small>₺</small></div>
      </div>
    </div>

    <div class="footer">
      <div class="badge" id="status">Güncelleniyor…</div>
      <div class="marquee"><span>Fiyatlar anlıktır • Sarıkaya Kuyumculuk • Hayırlı işler dileriz •</span></div>
      <div id="updated">--</div>
    </div>
  </div>

<script>
  function fmtTL(n) {{
    if (n === null || n === undefined) return "--";
    return Number(n).toLocaleString("tr-TR");
  }}

  function tickClock() {{
    const d = new Date();
    const hh = String(d.getHours()).padStart(2,"0");
    const mm = String(d.getMinutes()).padStart(2,"0");
    document.getElementById("clock").textContent = `${{hh}}:${{mm}}`;
  }}
  setInterval(tickClock, 1000);
  tickClock();

  async function refresh() {{
    try {{
      const r = await fetch("/prices", {{ cache: "no-store" }});
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();

      document.getElementById("ceyrek").textContent = fmtTL(j.ceyrek);
      document.getElementById("yarim").textContent  = fmtTL(j.yarim);
      document.getElementById("tam").textContent    = fmtTL(j.tam);

      const dt = new Date((j.updated_at_unix || Date.now()/1000) * 1000);
      document.getElementById("updated").textContent =
        "Son güncelleme: " + dt.toLocaleTimeString("tr-TR", {{hour:"2-digit", minute:"2-digit"}});

      document.getElementById("status").textContent = "Canlı";
    }} catch(e) {{
      document.getElementById("status").textContent = "Bağlantı sorunu";
    }}
  }}

  // TV tarafı yenileme sıklığı (10 sn ideal)
  refresh();
  setInterval(refresh, 10000);

  // TV’de tam ekran kolaylığı: /tv?fs=1
  const params = new URLSearchParams(location.search);
  if (params.get("fs") === "1" && document.documentElement.requestFullscreen) {{
    document.documentElement.requestFullscreen().catch(()=>{{}});
  }}
</script>
</body>
</html>
"""
    return HTMLResponse(html)