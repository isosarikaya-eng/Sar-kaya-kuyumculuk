import time
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

app = FastAPI()

# --- AYARLAR ---
OZBAG_API_URL = "https://api.ozbag.com/api/altin"
CACHE_TTL_SECONDS = 60  # 60 sn cache (Google Sheets çok çağırdığı için iyi olur)

# Basit in-memory cache
_cache: Dict[str, Any] = {
    "ts": 0.0,
    "data": None,
}


def _get_ozbag_data_cached() -> Tuple[Any, bool]:
    """
    Ozbağ API verisini cache'li döndürür.
    Returns: (data, from_cache)
    """
    now = time.time()
    ts = float(_cache.get("ts") or 0.0)

    if _cache.get("data") is not None and (now - ts) < CACHE_TTL_SECONDS:
        return _cache["data"], True

    try:
        r = requests.get(
            OZBAG_API_URL,
            timeout=15,
            headers={"User-Agent": "ozbag-scraper/1.0"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ozbağ API okunamadı: {e}")

    _cache["ts"] = now
    _cache["data"] = data
    return data, False


def _to_float(val: Any) -> Optional[float]:
    """
    '12.345,67' veya '12345.67' gibi değerleri float'a çevirir.
    Çeviremezse None döner.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    if not s:
        return None

    # Türkçe format desteği
    # Örn: 12.345,67 -> 12345.67
    s = s.replace("₺", "").replace("TL", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")

    try:
        return float(s)
    except Exception:
        return None


@app.get("/")
def health():
    return {"ok": True, "service": "ozbag-scraper", "cache_ttl_seconds": CACHE_TTL_SECONDS}


@app.get("/ozbag")
def ozbag():
    """
    Ozbağ API verisini JSON olarak döndürür.
    """
    data, from_cache = _get_ozbag_data_cached()
    return {"source": OZBAG_API_URL, "from_cache": from_cache, "data": data}


@app.get("/prices.csv", response_class=PlainTextResponse)
def prices_csv():
    """
    Google Sheets için CSV çıktısı üretir:
    kalem,fiyat
    Çeyrek,12150
    Yarım,24300
    """
    data, _ = _get_ozbag_data_cached()

    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="Beklenmeyen Ozbağ veri formatı (list değil).")

    lines = ["kalem,fiyat"]

    # Ozbağ API'nin alan adları değişebilir diye esnek okuyoruz.
    # Öncelik: satis -> satış fiyatı
    for item in data:
        if not isinstance(item, dict):
            continue

        name = item.get("name") or item.get("kalem") or item.get("urun") or item.get("title") or "Bilinmeyen"
        price_raw = (
            item.get("satis")
            or item.get("satış")
            or item.get("sell")
            or item.get("price")
            or item.get("fiyat")
        )

        price = _to_float(price_raw)

        # Eğer fiyat parse edilemezse boş geçiyoruz (istersen kaldırabiliriz)
        if price is None:
            continue

        # CSV virgül ayracı olduğu için isimde virgül varsa tırnaklayalım
        name_str = str(name)
        if "," in name_str or '"' in name_str:
            name_str = '"' + name_str.replace('"', '""') + '"'

        # Float'ı sade yaz (12150.0 -> 12150)
        if price.is_integer():
            price_str = str(int(price))
        else:
            price_str = str(price)

        lines.append(f"{name_str},{price_str}")

    return "\n".join(lines)