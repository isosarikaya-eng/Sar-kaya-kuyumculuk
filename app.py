import os
import re
import time
from typing import Dict, Any, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

app = FastAPI()

OZBAG_URL = "https://ozbag.com/"

# --- Basit cache (Railway için hayat kurtarır) ---
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))
_cache: Dict[str, Tuple[float, Any]] = {}  # key -> (ts, data)


def _cache_get(key: str):
    item = _cache.get(key)
    if not item:
        return None
    ts, data = item
    if time.time() - ts <= CACHE_TTL_SECONDS:
        return data
    return None


def _cache_set(key: str, data: Any):
    _cache[key] = (time.time(), data)


def fetch_ozbag_page() -> str:
    """
    Ozbağ sayfasını Playwright ile açıp HTML'i döner.
    Railway gibi container ortamında stabil olması için:
    - headless True
    - no-sandbox / disable-dev-shm-usage
    - timeout + küçük wait
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                locale="tr-TR",
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            # 60 sn timeout
            page.goto(OZBAG_URL, timeout=60000, wait_until="domcontentloaded")

            # Bazı siteler fiyatları JS ile sonradan basıyor: küçük bekleme
            page.wait_for_timeout(3000)

            html = page.content()

            context.close()
            browser.close()

            return html

    except PlaywrightTimeoutError:
        raise HTTPException(status_code=503, detail="Playwright timeout: ozbag.com geç yanıt verdi.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Playwright error: {type(e).__name__}: {e}")


def _normalize_money(s: str) -> str:
    """
    '12.345,67' gibi TR formatını normalize edip '12345.67' haline getirir.
    """
    s = s.strip()
    s = s.replace("₺", "").replace("TL", "").replace("tl", "").strip()
    # 12.345,67 -> 12345.67
    s = s.replace(".", "").replace(",", ".")
    return s


def parse_prices_from_html(html: str) -> Dict[str, Any]:
    """
    HTML içinden fiyatları yakalamaya çalışır.
    1) Sayfanın text halini çıkarır (tag'leri atar gibi).
    2) "Kalem ... fiyat" tarzı eşleştirme dener.
    3) Olmazsa ham bulunan para değerlerini listeler.
    """
    # Çok basit bir "text çıkarma"
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Para yakalama (TR format + TL/₺ opsiyonel)
    # Örn: 12.345,67  |  123.456  |  123.456,00  |  12345
    money_re = re.compile(r"(?:₺\s*)?\b\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?\b(?:\s*(?:TL|tl))?")

    found_money = money_re.findall(text)
    found_money = [m.strip() for m in found_money]
    # Duplicates temizle ama sıralamayı bozmayalım
    seen = set()
    found_money_unique = []
    for m in found_money:
        if m not in seen:
            seen.add(m)
            found_money_unique.append(m)

    # Kuyumcu kalemleri (senin sheet’teki gibi)
    keywords = [
        "Çeyrek", "Yarım", "Tam", "Gramse", "Ata", "Ata lira",
        "Gram Altın", "Gram", "22 Ayar", "24 Ayar", "Has",
    ]

    # Etiket-fiyat eşleştirme:
    # "Çeyrek ... 12.150" veya "Çeyrek 12150" gibi yakın geçenleri yakalamaya çalışır.
    result: Dict[str, Any] = {}
    for kw in keywords:
        # kw’den sonra 0-40 karakter içinde bir para değeri ara
        pattern = re.compile(rf"({re.escape(kw)})\s{{0,40}}({money_re.pattern})", re.IGNORECASE)
        m = pattern.search(text)
        if m:
            label = m.group(1).strip()
            money = m.group(2).strip()
            result[label] = money

    return {
        "source": OZBAG_URL,
        "items": result,                 # eşleştirebildiklerimiz
        "raw_found": found_money_unique, # sayfada gördüğümüz ham fiyatlar
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
    }


def get_prices() -> Dict[str, Any]:
    cached = _cache_get("ozbag_prices")
    if cached:
        return cached

    html = fetch_ozbag_page()
    data = parse_prices_from_html(html)
    _cache_set("ozbag_prices", data)
    return data


@app.get("/")
def health():
    return {"ok": True, "service": "ozbag-scraper", "cache_ttl_seconds": CACHE_TTL_SECONDS}


@app.get("/ozbag")
def ozbag():
    return get_prices()


@app.get("/prices.csv", response_class=PlainTextResponse)
def prices_csv():
    """
    Google Sheets: =IMPORTDATA("https://.../prices.csv")
    CSV format: kalem,fiyat
    """
    data = get_prices()
    items: Dict[str, str] = data.get("items", {})

    lines = ["kalem,fiyat"]

    # Eğer eşleştirme boşsa, en azından ham bulunanları dökelim
    if not items:
        raw = data.get("raw_found", [])[:30]
        for i, m in enumerate(raw, start=1):
            lines.append(f"bulunan_{i},{m}")
        return "\n".join(lines)

    # Eşleşenleri yaz
    for k, v in items.items():
        lines.append(f"{k},{v}")

    return "\n".join(lines)