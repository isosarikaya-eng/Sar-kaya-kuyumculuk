import os
import time
import json
import re
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse


# -----------------------------
# AYARLAR (ENV)
# -----------------------------
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))
OZBAG_API_URL = (os.getenv("OZBAG_API_URL") or "").strip()  # varsa gerçek JSON endpoint
OZBAG_SITE_URL = (os.getenv("OZBAG_SITE_URL") or "https://www.ozbag.com").strip()
LOGO_URL = (os.getenv("LOGO_URL") or "/static/logo.png").strip()

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "6.0"))

# "YEDEK" değerleri (API/HTML olmazsa ekranda boş kalmasın)
# Dilersen env ile de verebilirsin.
FALLBACK = {
    "ceyrek": 12150,
    "yarim": 24300,
    "tam": 48600,
}

# Basit cache
_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


app = FastAPI(title="Sarıkaya Kuyumculuk TV", version="1.0.0")


# -----------------------------
# YARDIMCILAR
# -----------------------------
def _now_ts() -> float:
    return time.time()


def _fmt_tr_int(n: int) -> str:
    # 48600 -> 48.600
    s = f"{n:,}".replace(",", ".")
    return s


def _safe_get(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Return: (text, error)
    """
    try:
        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": "SarıkayaTV/1.0 (+https://sarıkaya)"
            },
        )
        r.raise_for_status()
        return r.text, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _try_json_api(url: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Beklenen JSON örnekleri çok değişebileceği için esnek okuyoruz.
    Şu anahtarları arıyoruz: ceyrek/yarim/tam gibi.
    """
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        # data direkt dict ise:
        if isinstance(data, dict):
            return data, None

        # liste ise ilk dict'i al
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0], None

        return None, "JSON formatı beklenenden farklı (dict/list değil)."
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _extract_prices_from_any_json(data: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """
    JSON içinden ceyrek/yarim/tam bulmaya çalışır.
    - Direkt anahtarlar
    - Büyük/küçük farkı
    - İç içe yapılar
    """
    def norm_key(k: str) -> str:
        return k.lower().replace(" ", "").replace("_", "")

    target_keys = {
        "ceyrek": {"ceyrek", "çeyrek", "ceyrekaltin", "çeyrekaltın"},
        "yarim": {"yarim", "yarım", "yarimaltin", "yarımaltın"},
        "tam": {"tam", "tek", "tamaltin", "cumhuriyet", "ata"},
    }

    found: Dict[str, int] = {}

    # flatten (basit)
    stack = [data]
    seen = set()

    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))

        if isinstance(cur, dict):
            for k, v in cur.items():
                nk = norm_key(str(k))
                # Değer sayıya benziyorsa al
                if isinstance(v, (int, float, str)):
                    sv = str(v)
                    sv = sv.replace(".", "").replace(",", ".")  # kaba temizlik
                    m = re.search(r"(\d+(\.\d+)?)", sv)
                    if m:
                        val = int(float(m.group(1)))
                        for out_key, variants in target_keys.items():
                            if nk in {norm_key(x) for x in variants}:
                                found[out_key] = val
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            for it in cur:
                if isinstance(it, (dict, list)):
                    stack.append(it)

    if all(k in found for k in ("ceyrek", "yarim", "tam")):
        return found
    return None


def _extract_prices_from_html(html: str) -> Optional[Dict[str, int]]:
    """
    ozbag.com HTML içinden fiyatları yakalamaya çalışır.
    Burada kesin şablon bilmediğimiz için birkaç regex yaklaşımı deniyoruz.
    """
    text = html

    # 1) "Çeyrek" yakınındaki sayıları yakala (en yaygın)
    def grab_near(label: str) -> Optional[int]:
        # label yakınında 3-80 karakter içinde 12.150 / 12150 / 12,150 gibi bir sayı ara
        pattern = rf"{label}.{{0,120}}?(\d{{1,3}}([.,]\d{{3}})+|\d{{4,6}})"
        m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        raw = m.group(1)
        raw = raw.replace(".", "").replace(",", "")
        try:
            return int(raw)
        except:
            return None

    c = grab_near("çeyrek") or grab_near("ceyrek")
    y = grab_near("yarım") or grab_near("yarim")
    t = grab_near("tam") or grab_near("cumhuriyet") or grab_near("ata")

    if c and y and t:
        return {"ceyrek": c, "yarim": y, "tam": t}

    # 2) Son çare: sayıları topla/akıllı seç (çok riskli ama boş kalmasın diye)
    nums = re.findall(r"(\d{1,3}(?:[.,]\d{3})+)", text)
    parsed = []
    for n in nums:
        try:
            parsed.append(int(n.replace(".", "").replace(",", "")))
        except:
            pass

    parsed = sorted(set([p for p in parsed if 1000 < p < 500000]))
    if len(parsed) >= 3:
        # mantık: genelde küçük=çeyrek, orta=yarım, büyük=tam
        return {"ceyrek": parsed[0], "yarim": parsed[1], "tam": parsed[2]}

    return None


def _build_payload(prices: Dict[str, int], source: str) -> Dict[str, Any]:
    now_local = time.strftime("%H:%M:%S")
    return {
        "source": source,  # API / OZBAG-HTML / CACHE / YEDEK
        "updated_at": now_local,
        "prices": {
            "ceyrek": int(prices["ceyrek"]),
            "yarim": int(prices["yarim"]),
            "tam": int(prices["tam"]),
        },
    }


def _get_prices_live() -> Dict[str, Any]:
    # Cache kontrol
    age = _now_ts() - float(_cache["ts"] or 0.0)
    if _cache["data"] is not None and age < CACHE_TTL_SECONDS:
        data = dict(_cache["data"])
        data["source"] = "CACHE"
        return data

    # 1) OZBAG_API_URL varsa JSON dene
    if OZBAG_API_URL:
        j, err = _try_json_api(OZBAG_API_URL)
        if j is not None:
            extracted = _extract_prices_from_any_json(j)
            if extracted:
                payload = _build_payload(extracted, "API")
                _cache["ts"] = _now_ts()
                _cache["data"] = payload
                return payload

    # 2) HTML scrape (endpoint yoksa bile)
    html, err = _safe_get(OZBAG_SITE_URL)
    if html:
        extracted = _extract_prices_from_html(html)
        if extracted:
            payload = _build_payload(extracted, "OZBAG-HTML")
            _cache["ts"] = _now_ts()
            _cache["data"] = payload
            return payload

    # 3) En son: YEDEK
    payload = _build_payload(FALLBACK, "YEDEK")
    _cache["ts"] = _now_ts()
    _cache["data"] = payload
    return payload


# -----------------------------
# ROUTES
# -----------------------------
@app.get("/health", response_class=JSONResponse)
def health():
    return {"ok": True, "cache_ttl": CACHE_TTL_SECONDS}


@app.get("/prices", response_class=JSONResponse)
def prices():
    return _get_prices_live()


@app.get("/tv", response_class=HTMLResponse)
def tv():
    # Tek dosya olsun diye HTML'yi burada basıyoruz.
    # Frontend 5 sn'de bir /prices çeker.
    return f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sarıkaya Kuyumculuk • TV</title>
  <style>
    :root {{
      --bg: #06070a;
      --card: rgba(255,255,255,0.04);
      --stroke: rgba(209,170,84,0.35);
      --gold: #d1aa54;
      --text: #f5f6f7;
      --muted: rgba(255,255,255,0.65);
    }}
    body {{
      margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      background: radial-gradient(1200px 700px at 20% 10%, rgba(209,170,84,0.08), transparent 60%),
                  radial-gradient(900px 600px at 80% 20%, rgba(255,255,255,0.05), transparent 55%),
                  var(--bg);
      color: var(--text);
    }}
    .wrap {{ padding: 28px 28px 18px; }}
    .top {{
      display:flex; align-items:center; justify-content:space-between; gap:16px;
    }}
    .brand {{
      display:flex; align-items:center; gap:14px;
    }}
    .logo {{
      width:44px; height:44px; border-radius:14px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.08);
      display:flex; align-items:center; justify-content:center;
      overflow:hidden;
    }}
    .logo img {{ width:100%; height:100%; object-fit:cover; }}
    .title {{
      font-weight:800; letter-spacing:0.06em; color: var(--gold);
      font-size: 34px; line-height: 1.05;
      text-transform: uppercase;
    }}
    .subtitle {{ color: var(--muted); margin-top: 6px; font-size: 18px; }}
    .right {{
      text-align:right;
    }}
    .clock {{ font-size: 56px; font-weight: 800; letter-spacing: 0.02em; }}
    .date {{ color: var(--muted); font-size: 18px; margin-top: 4px; }}
    .grid {{
      display:grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 22px;
      margin-top: 26px;
    }}
    .card {{
      border-radius: 28px;
      background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));
      border: 1px solid var(--stroke);
      padding: 22px 22px 18px;
      min-height: 240px;
      position: relative;
      overflow:hidden;
    }}
    .label {{
      color: rgba(255,255,255,0.75);
      font-weight: 800;
      letter-spacing: 0.22em;
      font-size: 20px;
    }}
    .price {{
      margin-top: 34px;
      font-size: 84px;
      font-weight: 900;
      letter-spacing: 0.01em;
    }}
    .tl {{
      color: var(--gold);
      font-size: 54px;
      font-weight: 900;
      margin-left: 10px;
    }}
    .update {{
      position:absolute; left:22px; bottom:16px;
      color: rgba(255,255,255,0.45);
      font-size: 16px;
    }}
    .footer {{
      display:flex; gap:12px; margin-top: 18px; align-items:center; justify-content:space-between;
      color: rgba(255,255,255,0.6);
    }}
    .pill {{
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.04);
      padding: 10px 14px;
      border-radius: 18px;
      display:flex; align-items:center; gap:10px;
      font-size: 16px;
    }}
    .dot {{
      width:10px; height:10px; border-radius: 50%;
      background: #33d17a;
    }}
    .dot.red {{ background: #ff4d4d; }}
    @media (max-width: 900px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .clock {{ font-size: 44px; }}
      .price {{ font-size: 64px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo" title="Logo">
          <img src="{LOGO_URL}" onerror="this.style.display='none'; this.parentElement.textContent='SK';" />
        </div>
        <div>
          <div class="title">SARIKAYA<br/>KUYUMCULUK</div>
          <div class="subtitle">Canlı Fiyat Ekranı • TV</div>
        </div>
      </div>
      <div class="right">
        <div class="clock" id="clock">--:--</div>
        <div class="date" id="date">--</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="label">ÇEYREK</div>
        <div class="price"><span id="ceyrek">-</span><span class="tl">₺</span></div>
        <div class="update">Güncelleme: <span id="u1">-</span></div>
      </div>
      <div class="card">
        <div class="label">YARIM</div>
        <div class="price"><span id="yarim">-</span><span class="tl">₺</span></div>
        <div class="update">Güncelleme: <span id="u2">-</span></div>
      </div>
      <div class="card">
        <div class="label">TAM</div>
        <div class="price"><span id="tam">-</span><span class="tl">₺</span></div>
        <div class="update">Güncelleme: <span id="u3">-</span></div>
      </div>
    </div>

    <div class="footer">
      <div class="pill"><span class="dot" id="dot"></span><span id="auto">Otomatik güncelleniyor</span></div>
      <div class="pill">Kaynak: <b id="src">-</b></div>
      <div class="pill">Son güncelleme: <b id="last">-</b></div>
      <div class="pill">Hayırlı işler dileriz</div>
    </div>
  </div>

<script>
function pad(n) {{ return String(n).padStart(2,'0'); }}

function tickClock() {{
  const d = new Date();
  document.getElementById('clock').textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
  const days = ["Pazar","Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi"];
  const months = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran","Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"];
  document.getElementById('date').textContent = d.getDate() + " " + months[d.getMonth()] + " " + d.getFullYear() + " • " + days[d.getDay()];
}}
setInterval(tickClock, 1000);
tickClock();

function formatTR(num) {{
  const s = String(num);
  // already int, convert 48600 -> 48.600
  return s.replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ".");
}}

async function refreshPrices() {{
  try {{
    const r = await fetch('/prices', {{cache:'no-store'}});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();

    const p = data.prices || {{}};
    document.getElementById('ceyrek').textContent = p.ceyrek ? formatTR(p.ceyrek) : '-';
    document.getElementById('yarim').textContent  = p.yarim ? formatTR(p.yarim) : '-';
    document.getElementById('tam').textContent    = p.tam ? formatTR(p.tam) : '-';

    document.getElementById('u1').textContent = data.updated_at || '-';
    document.getElementById('u2').textContent = data.updated_at || '-';
    document.getElementById('u3').textContent = data.updated_at || '-';

    document.getElementById('src').textContent = data.source || '-';
    document.getElementById('last').textContent = data.updated_at || '-';

    const dot = document.getElementById('dot');
    if ((data.source || '').toUpperCase().includes('YEDEK')) {{
      dot.classList.add('red');
    }} else {{
      dot.classList.remove('red');
    }}
  }} catch(e) {{
    const dot = document.getElementById('dot');
    dot.classList.add('red');
    document.getElementById('src').textContent = 'HATA';
  }}
}}

refreshPrices();
setInterval(refreshPrices, 5000);
</script>
</body>
</html>
""".strip()