import os
import json
import re
import time
import asyncio
from typing import Dict, Any, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from playwright.async_api import async_playwright


# =========================
# ENV / AYARLAR
# =========================
OZBAG_SITE_URL = os.getenv("OZBAG_SITE_URL", "https://www.ozbag.com/").strip()
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))
LOGO_URL = os.getenv("LOGO_URL", "/static/logo.png").strip()

# Marj eklemek için opsiyonel:
# Örnek:
# MARGIN_JSON='{"ESKI_CEYREK":{"buy":0,"sell":50},"ONS_ALTIN":{"buy":0,"sell":0}}'
MARGIN_JSON_RAW = os.getenv("MARGIN_JSON", "").strip()
try:
    MARGIN = json.loads(MARGIN_JSON_RAW) if MARGIN_JSON_RAW else {}
except Exception:
    MARGIN = {}

# Hangi ürünleri gösteriyoruz?
# key: API’de dönen anahtar
# label: ekranda görünen başlık
# ozbag_row: tabloda satır ismi (sarrafiye tablosu)
# type: "sarrafiye_eski" veya "ons"
ITEMS = [
    {"key": "ESKI_CEYREK", "label": "Eski Çeyrek", "ozbag_row": "ÇEYREK", "type": "sarrafiye_eski"},
    {"key": "ESKI_YARIM",  "label": "Eski Yarım",  "ozbag_row": "YARIM",  "type": "sarrafiye_eski"},
    {"key": "ESKI_TAM",    "label": "Eski Tam",    "ozbag_row": "TAM",    "type": "sarrafiye_eski"},
    {"key": "ESKI_GREMSE", "label": "Eski Gremse", "ozbag_row": "GREMSE", "type": "sarrafiye_eski"},
    {"key": "ESKI_ATA",    "label": "Eski Ata",    "ozbag_row": "ATA",    "type": "sarrafiye_eski"},
    {"key": "ONS_ALTIN",   "label": "Ons Altın",   "ozbag_row": None,     "type": "ons"},
]


app = FastAPI(title="Sarıkaya Kuyumculuk TV")


# =========================
# BASİT CACHE
# =========================
_cache: Dict[str, Any] = {
    "ts": 0,
    "data": None,
    "source": "YOK",
    "error": None,
    "last_ok_ts": 0,
}


def _now() -> int:
    return int(time.time())


def _to_float(s: str) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    # 7.436.287 gibi (binlik .) veya 7,436.287 gibi karışık gelebilir
    # TR format: 12,150 -> 12150
    s = s.replace("₺", "").replace("$", "").replace("€", "").strip()
    s = s.replace(".", "").replace(",", ".")  # 12.150 -> 12150, 7,436 -> 7.436
    try:
        return float(s)
    except Exception:
        return None


def _apply_margin(item_key: str, buy: Optional[float], sell: Optional[float]) -> (Optional[float], Optional[float]):
    m = MARGIN.get(item_key, {}) if isinstance(MARGIN, dict) else {}
    mb = _to_float(m.get("buy")) if isinstance(m, dict) else None
    ms = _to_float(m.get("sell")) if isinstance(m, dict) else None
    if buy is not None and mb is not None:
        buy = buy + mb
    if sell is not None and ms is not None:
        sell = sell + ms
    return buy, sell


# =========================
# OZBAG SCRAPE (PLAYWRIGHT)
# =========================
async def fetch_ozbag_prices():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        page = await browser.new_page()

        await page.goto("https://www.ozbag.com/", wait_until="networkidle")

        await page.wait_for_selector("table")

        rows = await page.query_selector_all("table tbody tr")

        data = {}

        for r in rows:

            cols = await r.query_selector_all("td")

            if len(cols) < 5:
                continue

            name = (await cols[0].inner_text()).strip()

            eski_alis = await cols[3].inner_text()
            eski_satis = await cols[4].inner_text()

            name = name.upper()

            if "ÇEYREK" in name:
                data["ESKI_CEYREK"] = {
                    "buy": _to_float(eski_alis),
                    "sell": _to_float(eski_satis)
                }

            if "YARIM" in name:
                data["ESKI_YARIM"] = {
                    "buy": _to_float(eski_alis),
                    "sell": _to_float(eski_satis)
                }

            if "TAM" in name:
                data["ESKI_TAM"] = {
                    "buy": _to_float(eski_alis),
                    "sell": _to_float(eski_satis)
                }

            if "GREMSE" in name:
                data["ESKI_GREMSE"] = {
                    "buy": _to_float(eski_alis),
                    "sell": _to_float(eski_satis)
                }

            if "ATA" in name:
                data["ESKI_ATA"] = {
                    "buy": _to_float(eski_alis),
                    "sell": _to_float(eski_satis)
                }

        await browser.close()

        return {
            "items": data,
            "updated_at": time.strftime("%H:%M:%S")
        }

        return {
            "items": results,
            "updated_at": updated_at,
        }


async def get_prices_cached(force: bool = False) -> Dict[str, Any]:
    fresh = (_now() - int(_cache["ts"])) < CACHE_TTL_SECONDS
    if (not force) and fresh and _cache["data"]:
        return {
            "ok": True,
            "source": _cache["source"],
            "data": _cache["data"],
            "error": _cache["error"],
            "ts": _cache["ts"],
            "last_ok_ts": _cache["last_ok_ts"],
        }

    try:
        data = await fetch_ozbag_prices()
        _cache["data"] = data
        _cache["ts"] = _now()
        _cache["last_ok_ts"] = _cache["ts"]
        _cache["source"] = "OZBAG"
        _cache["error"] = None
    except Exception as e:
        # Özbağ düşerse / 403 olursa: cache varsa onu göster
        _cache["ts"] = _now()
        _cache["source"] = "CACHE" if _cache["data"] else "YEDEK"
        _cache["error"] = str(e)

    return {
        "ok": _cache["data"] is not None,
        "source": _cache["source"],
        "data": _cache["data"],
        "error": _cache["error"],
        "ts": _cache["ts"],
        "last_ok_ts": _cache["last_ok_ts"],
    }


# =========================
# API
# =========================
@app.get("/api/prices")
async def api_prices():
    payload = await get_prices_cached(force=False)
    return JSONResponse(payload)


# =========================
# TV EKRANI
# =========================
@app.get("/tv", response_class=HTMLResponse)
async def tv():
    # İlk load’da da cache’i hazırla
    initial = await get_prices_cached(force=True)

    # HTML + CSS + JS tek dosyada
    html = f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Sarıkaya Kuyumculuk - Canlı Fiyat Ekranı</title>
  <style>
    :root {{
      --bg: #07070a;
      --card: rgba(255,255,255,0.06);
      --border: rgba(212,175,55,0.35);
      --gold: #d4af37;
      --text: #f2f2f2;
      --muted: rgba(255,255,255,0.55);
      --good: rgba(39, 174, 96, 0.55);
      --bad: rgba(231, 76, 60, 0.55);
    }}
    * {{ box-sizing: border-box; font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Inter,Arial,sans-serif; }}
    body {{
      margin:0; color:var(--text);
      background: radial-gradient(1200px 600px at 20% 10%, rgba(212,175,55,0.15), transparent 60%),
                  radial-gradient(900px 500px at 80% 30%, rgba(212,175,55,0.10), transparent 60%),
                  var(--bg);
      overflow-x:hidden;
    }}
    .top {{
      display:flex; justify-content:space-between; align-items:flex-end;
      padding: 28px 32px 10px 32px;
    }}
    .brand {{
      display:flex; gap:14px; align-items:center;
    }}
    .logo {{
      width:48px; height:48px; border-radius:14px;
      background: linear-gradient(180deg, rgba(255,255,255,0.09), rgba(255,255,255,0.03));
      border:1px solid rgba(255,255,255,0.10);
      display:flex; align-items:center; justify-content:center;
      color:var(--gold); font-weight:800;
    }}
    .title h1 {{ margin:0; font-size:34px; letter-spacing:0.5px; color:var(--gold); }}
    .title .sub {{ margin-top:4px; color:var(--muted); font-size:16px; }}
    .clock {{
      text-align:right;
    }}
    .clock .time {{ font-size:64px; font-weight:800; line-height:1; }}
    .clock .date {{ color:var(--muted); margin-top:6px; font-size:18px; }}
    .grid {{
      display:grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      padding: 18px 32px 32px 32px;
      max-width: 1100px;
    }}
    @media (min-width: 980px) {{
      .grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 22px;
      padding: 18px;
      box-shadow: 0 12px 40px rgba(0,0,0,0.30);
      position:relative;
      overflow:hidden;
    }}
    .card:before {{
      content:"";
      position:absolute; inset:-2px;
      background: radial-gradient(600px 240px at 20% 10%, rgba(212,175,55,0.22), transparent 60%);
      opacity:0.35;
      pointer-events:none;
    }}
    .card h2 {{
      margin:0 0 14px 0;
      font-size: 22px;
      letter-spacing: 0.8px;
      color: rgba(255,255,255,0.78);
    }}
    .row {{
      display:grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .box {{
      border-radius: 18px;
      background: rgba(0,0,0,0.22);
      border: 1px solid rgba(255,255,255,0.10);
      padding: 14px 14px 10px 14px;
      min-height: 92px;
      position:relative;
      overflow:hidden;
    }}
    .box .lbl {{
      color: rgba(255,255,255,0.55);
      font-size: 13px;
      letter-spacing: 2px;
      text-transform: uppercase;
    }}
    .box .val {{
      margin-top: 10px;
      font-size: 34px;
      font-weight: 800;
      letter-spacing: 0.6px;
      display:flex;
      align-items:baseline;
      gap:10px;
    }}
    .box .val .cur {{ color: var(--gold); font-weight:800; }}
    .meta {{
      display:flex; gap:10px; flex-wrap:wrap;
      padding: 0 32px 30px 32px;
      max-width: 1100px;
    }}
    .pill {{
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.10);
      color: rgba(255,255,255,0.75);
      border-radius: 999px;
      padding: 10px 14px;
      font-size: 14px;
      display:flex; align-items:center; gap:10px;
    }}
    .dot {{
      width:10px; height:10px; border-radius:50%;
      background: #2ecc71;
      box-shadow: 0 0 10px rgba(46,204,113,0.7);
    }}
    .dot.red {{
      background:#e74c3c;
      box-shadow: 0 0 10px rgba(231,76,60,0.7);
    }}
    .flash-up {{ animation: flashUp 0.6s ease-out; }}
    .flash-down {{ animation: flashDown 0.6s ease-out; }}
    @keyframes flashUp {{
      0% {{ background: rgba(39,174,96,0.45); }}
      100% {{ background: rgba(0,0,0,0.22); }}
    }}
    @keyframes flashDown {{
      0% {{ background: rgba(231,76,60,0.45); }}
      100% {{ background: rgba(0,0,0,0.22); }}
    }}
    .footer {{
      padding: 0 32px 26px 32px;
      color: rgba(255,255,255,0.35);
      max-width: 1100px;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <div class="top">
    <div class="brand">
      <div class="logo">SK</div>
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

  <div class="grid" id="grid"></div>

  <div class="meta">
    <div class="pill"><span class="dot" id="autoDot"></span><span id="autoTxt">Otomatik güncelleniyor</span></div>
    <div class="pill">Kaynak: <b id="srcTxt">{initial.get("source","-")}</b></div>
    <div class="pill">Son güncelleme: <b id="updTxt">--</b></div>
    <div class="pill" id="errPill" style="display:none;">Hata: <span id="errTxt"></span></div>
    <div class="pill" style="margin-left:auto;">Hayırlı işler dileriz</div>
  </div>

  <div class="footer">
    Not: Özbağ erişilemezse sistem otomatik olarak Cache/Yedek verisiyle devam eder.
  </div>

<script>
  const ITEMS = {json.dumps([{ "key": i["key"], "label": i["label"] } for i in ITEMS], ensure_ascii=False)};

  let prev = {{}};

  function fmt(v) {{
    if (v === null || v === undefined || isNaN(v)) return "--";
    const n = Number(v);
    // TR para formatı: 12.150
    return n.toLocaleString("tr-TR", {{ maximumFractionDigits: 0 }});
  }}

  function build() {{
    const grid = document.getElementById("grid");
    grid.innerHTML = "";
    for (const it of ITEMS) {{
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <h2>${{it.label}}</h2>
        <div class="row">
          <div class="box" data-k="${{it.key}}:buy">
            <div class="lbl">ALIŞ</div>
            <div class="val"><span class="num">--</span> <span class="cur">₺</span></div>
          </div>
          <div class="box" data-k="${{it.key}}:sell">
            <div class="lbl">SATIŞ</div>
            <div class="val"><span class="num">--</span> <span class="cur">₺</span></div>
          </div>
        </div>
      `;
      grid.appendChild(card);
    }}
  }}

  function setBox(key, value) {{
    const box = document.querySelector(`.box[data-k="${{key}}"]`);
    if (!box) return;
    const num = box.querySelector(".num");
    const old = prev[key];
    num.textContent = fmt(value);

    if (old !== undefined && value !== null && value !== undefined && !isNaN(value) && old !== value) {{
      box.classList.remove("flash-up", "flash-down");
      void box.offsetWidth; // reflow
      if (value > old) box.classList.add("flash-up");
      else box.classList.add("flash-down");
    }}
    prev[key] = value;
  }}

  function updateClock() {{
    const d = new Date();
    const t = d.toLocaleTimeString("tr-TR", {{hour:"2-digit", minute:"2-digit"}});
    const day = d.toLocaleDateString("tr-TR", {{ day:"numeric", month:"long", year:"numeric", weekday:"long" }});
    document.getElementById("clockTime").textContent = t;
    document.getElementById("clockDate").textContent = day;
  }}

  async function tick() {{
    try {{
      const r = await fetch("/api/prices", {{ cache: "no-store" }});
      const j = await r.json();

      document.getElementById("srcTxt").textContent = j.source || "-";
      const ok = !!j.ok;
      const dot = document.getElementById("autoDot");
      dot.classList.toggle("red", !ok);
      document.getElementById("autoTxt").textContent = ok ? "Otomatik güncelleniyor" : "Cache/Yedek kullanılıyor";

      // hata
      const errPill = document.getElementById("errPill");
      if (j.error) {{
        errPill.style.display = "flex";
        document.getElementById("errTxt").textContent = j.error;
      }} else {{
        errPill.style.display = "none";
      }}

      if (j.data && j.data.items) {{
        // güncelleme saati
        document.getElementById("updTxt").textContent = (j.data.updated_at || new Date().toLocaleTimeString("tr-TR"));

        for (const it of ITEMS) {{
          const obj = j.data.items[it.key] || {{}};
          setBox(`${{it.key}}:buy`, obj.buy);
          setBox(`${{it.key}}:sell`, obj.sell);
        }}
      }}
    }} catch (e) {{
      const dot = document.getElementById("autoDot");
      dot.classList.add("red");
      document.getElementById("autoTxt").textContent = "Cache/Yedek kullanılıyor";
      const errPill = document.getElementById("errPill");
      errPill.style.display = "flex";
      document.getElementById("errTxt").textContent = String(e);
    }}
  }}

  build();
  updateClock();
  setInterval(updateClock, 1000);
  tick();
  setInterval(tick, {max(5, CACHE_TTL_SECONDS)} * 1000);
</script>
</body>
</html>
"""
    return HTMLResponse(html)