import os
import re
import json
import time
import asyncio
from typing import Dict, Any, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

app = FastAPI()

OZBAG_SITE_URL = os.getenv("OZBAG_SITE_URL", "https://www.ozbag.com/").strip()
CACHE_TTL = int(os.getenv("CACHE_TTL", "30"))
LOGO_URL = os.getenv("LOGO_URL", "/static/logo.png")
MARGIN_JSON = os.getenv("MARGIN_JSON", "").strip()

# ---- Cache state ----
_cache: Dict[str, Any] = {
    "source": "YEDEK",
    "updated_at": None,
    "data": {}
}
_last_ok: Optional[float] = None

# ---- Helpers ----
def _now_ts() -> int:
    return int(time.time())

def _safe_float(s: str) -> Optional[float]:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    # "7.436,287" -> 7436.287
    s = s.replace(".", "").replace(",", ".")
    s = re.sub(r"[^\d\.]", "", s)
    try:
        return float(s)
    except:
        return None

def _apply_margins(prices: Dict[str, Dict[str, Optional[float]]]) -> Dict[str, Dict[str, Optional[float]]]:
    """
    MARGIN_JSON örneği:
    {
      "ESKI_CEYREK": {"buy": 0, "sell": 0},
      "ESKI_YARIM": {"buy": 0, "sell": 0}
    }
    (İstersen sonra beraber netleştiririz; boşsa dokunmaz.)
    """
    if not MARGIN_JSON:
        return prices
    try:
        margins = json.loads(MARGIN_JSON)
    except:
        return prices

    out = {}
    for key, val in prices.items():
        mb = margins.get(key, {}).get("buy", 0)
        ms = margins.get(key, {}).get("sell", 0)
        b = val.get("buy")
        s = val.get("sell")
        out[key] = {
            "buy": (b + mb) if isinstance(b, (int, float)) else b,
            "sell": (s + ms) if isinstance(s, (int, float)) else s,
        }
    return out

# ---- Scrape (Playwright) ----
async def _fetch_ozbag_once() -> Dict[str, Any]:
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            user_agent=ua,
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"},
        )
        page = await context.new_page()

        # Çok kritik: sonsuza kadar bekleme yok
        await page.goto(OZBAG_SITE_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1200)
        html = await page.content()

        await context.close()
        await browser.close()

    soup = BeautifulSoup(html, "lxml")

    # Ozbağ ana sayfadaki “Sarrafiye” tablosundan çekmeye çalışıyoruz.
    # Sitede değişiklik olursa bile: hata atmak yerine None döner.
    def pick_row(label_text: str) -> Dict[str, Optional[float]]:
        # label_text: "ÇEYREK", "YARIM", "TAM", "GREMSE", "ATA"
        text = soup.get_text(" ", strip=True).upper()
        if label_text.upper() not in text:
            return {"buy": None, "sell": None}

        # Basit ama dayanıklı yaklaşım: label’dan sonra gelen ilk 2 fiyatı yakala.
        # (UI değişirse yine None’a düşer, sayfa boş kalmaz.)
        pattern = re.compile(rf"{re.escape(label_text.upper())}.*?₺\s*([\d\.\,]+).*?₺\s*([\d\.\,]+)", re.DOTALL)
        m = pattern.search(html.upper())
        if not m:
            return {"buy": None, "sell": None}
        return {"buy": _safe_float(m.group(1)), "sell": _safe_float(m.group(2))}

    prices = {
        "ESKI_CEYREK": pick_row("ÇEYREK"),
        "ESKI_YARIM": pick_row("YARIM"),
        "ESKI_TAM": pick_row("TAM"),
        "ESKI_GREMSE": pick_row("GREMSE"),
        "ESKI_ATA": pick_row("ATA"),
        # Ons paneli ayrı olabilir; bulamazsa None
        "ONS_ALTIN": {"buy": None, "sell": None},
    }

    prices = _apply_margins(prices)

    return {
        "source": "OZBAG",
        "updated_at": _now_ts(),
        "data": prices,
    }

async def _updater_loop():
    global _cache, _last_ok
    while True:
        try:
            data = await _fetch_ozbag_once()
            # Eğer hiç fiyat yakalayamadıysa da “OZBAG” deyip ekranı bozmayalım:
            has_any = any(
                (v.get("buy") is not None or v.get("sell") is not None)
                for v in data["data"].values()
            )
            if has_any:
                _cache = data
                _last_ok = time.time()
            else:
                # kaynağı değiştirme, sadece hata yazma
                pass
        except Exception as e:
            # Çökme yok, sadece cache ile devam
            _cache["error"] = f"{type(e).__name__}: {str(e)}"
        await asyncio.sleep(CACHE_TTL)

@app.on_event("startup")
async def _startup():
    asyncio.create_task(_updater_loop())

# ---- Routes ----
@app.get("/api/health")
async def health():
    return {"ok": True, "source": _cache.get("source"), "updated_at": _cache.get("updated_at")}

@app.get("/api/prices")
async def api_prices():
    return JSONResponse(_cache)

@app.get("/", response_class=HTMLResponse)
@app.get("/tv", response_class=HTMLResponse)
async def tv():
    # Bu sayfa ASLA boş kalmaz. JS fiyatları /api/prices’ten çeker.
    return HTMLResponse(f"""
<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Canlı Fiyat Ekranı • TV</title>
<style>
  body {{ margin:0; background:#050505; color:#f2f2f2; font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial; }}
  .wrap {{ padding:24px; max-width: 1100px; margin:0 auto; }}
  .top {{ display:flex; align-items:center; justify-content:space-between; gap:16px; }}
  .brand {{ display:flex; align-items:center; gap:12px; opacity:.9; }}
  .logo {{ width:42px; height:42px; border-radius:10px; background:#111; display:flex; align-items:center; justify-content:center; }}
  .h1 {{ font-size:18px; opacity:.8; }}
  .clock {{ font-size:52px; font-weight:800; letter-spacing:1px; }}
  .grid {{ display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:18px; margin-top:18px; }}
  .card {{ border:1px solid rgba(255,215,80,.18); border-radius:18px; padding:18px; background: radial-gradient(1200px 600px at 10% 0%, rgba(255,215,80,.09), transparent 40%), #0b0b0b; }}
  .title {{ font-size:34px; font-weight:800; opacity:.9; }}
  .row {{ display:flex; gap:14px; margin-top:14px; }}
  .box {{ flex:1; border-radius:16px; background:#0f0f0f; border:1px solid rgba(255,255,255,.06); padding:14px; }}
  .lbl {{ font-size:12px; letter-spacing:2px; opacity:.65; }}
  .val {{ font-size:54px; font-weight:900; margin-top:8px; }}
  .meta {{ display:flex; gap:14px; flex-wrap:wrap; margin-top:16px; opacity:.85; }}
  .pill {{ border:1px solid rgba(255,255,255,.08); background:#0c0c0c; border-radius:999px; padding:10px 14px; }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:8px; background:#777; }}
  .dot.ok {{ background:#35d07f; }}
  .dot.bad {{ background:#ff4d4d; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand">
      <div class="logo">SK</div>
      <div>
        <div class="h1">Canlı Fiyat Ekranı • TV</div>
        <div id="source">Kaynak: YEDEK</div>
      </div>
    </div>
    <div>
      <div class="clock" id="clock">--:--</div>
      <div id="date" style="opacity:.7; text-align:right;"></div>
    </div>
  </div>

  <div class="grid" id="grid"></div>

  <div class="meta">
    <div class="pill"><span class="dot" id="statusDot"></span><span id="statusText">Otomatik güncelleniyor</span></div>
    <div class="pill" id="updatedAt">Son güncelleme: --</div>
    <div class="pill" id="errorBox" style="display:none;">Hata: -</div>
    <div class="pill" style="margin-left:auto;">Hayırlı işler dileriz</div>
  </div>
</div>

<script>
const labels = [
  ["ESKI_CEYREK","Eski Çeyrek"],
  ["ESKI_YARIM","Eski Yarım"],
  ["ESKI_TAM","Eski Tam"],
  ["ESKI_GREMSE","Eski Gremse"],
  ["ESKI_ATA","Eski Ata"],
  ["ONS_ALTIN","Ons Altın"],
];

function fmt(v) {{
  if (v === null || v === undefined) return "-";
  // büyük sayıları da okunur yap
  const s = String(v);
  return s.includes(".") ? Number(v).toLocaleString("tr-TR", {{minimumFractionDigits:0, maximumFractionDigits:3}}) : Number(v).toLocaleString("tr-TR");
}}

function renderGrid(data) {{
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  for (const [k, title] of labels) {{
    const item = (data && data[k]) || {{buy:null, sell:null}};
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="title">${{title}}</div>
      <div class="row">
        <div class="box">
          <div class="lbl">ALIŞ</div>
          <div class="val">${{fmt(item.buy)}}</div>
        </div>
        <div class="box">
          <div class="lbl">SATIŞ</div>
          <div class="val">${{fmt(item.sell)}}</div>
        </div>
      </div>`;
    grid.appendChild(card);
  }}
}}

async function loadPrices() {{
  const dot = document.getElementById("statusDot");
  const statusText = document.getElementById("statusText");
  const source = document.getElementById("source");
  const updatedAt = document.getElementById("updatedAt");
  const errorBox = document.getElementById("errorBox");

  try {{
    dot.className = "dot";
    statusText.textContent = "Güncelleniyor...";
    const r = await fetch("/api/prices", {{cache:"no-store"}});
    const j = await r.json();

    const src = j.source || "YEDEK";
    source.textContent = "Kaynak: " + src;

    renderGrid(j.data || {{}});

    if (j.updated_at) {{
      const d = new Date(j.updated_at*1000);
      updatedAt.textContent = "Son güncelleme: " + d.toLocaleTimeString("tr-TR");
    }} else {{
      updatedAt.textContent = "Son güncelleme: --";
    }}

    if (j.error) {{
      errorBox.style.display = "";
      errorBox.textContent = "Hata: " + j.error;
      dot.classList.add("bad");
      statusText.textContent = "Cache/Yedek kullanılıyor";
    }} else {{
      errorBox.style.display = "none";
      dot.classList.add("ok");
      statusText.textContent = (src === "OZBAG") ? "Otomatik güncelleniyor" : "Cache/Yedek kullanılıyor";
    }}
  }} catch (e) {{
    dot.className = "dot bad";
    statusText.textContent = "Cache/Yedek kullanılıyor";
    errorBox.style.display = "";
    errorBox.textContent = "Hata: " + e;
  }}
}}

function tick() {{
  const now = new Date();
  document.getElementById("clock").textContent = now.toLocaleTimeString("tr-TR", {{hour:"2-digit", minute:"2-digit"}});
  document.getElementById("date").textContent = now.toLocaleDateString("tr-TR", {{weekday:"long", day:"2-digit", month:"long", year:"numeric"}});
}}

tick();
setInterval(tick, 1000);
loadPrices();
setInterval(loadPrices, {CACHE_TTL * 1000});
</script>
</body>
</html>
""")