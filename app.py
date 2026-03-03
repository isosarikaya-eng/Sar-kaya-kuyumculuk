import re
from fastapi import FastAPI
from playwright.sync_api import sync_playwright

app = FastAPI()

OZBAG_URL = "https://ozbag.com/"

def parse_prices_from_html(html: str) -> dict:
    """
    Burada sayfadaki görünen metinden fiyatları çekiyoruz.
    Özbağ sayfasında etiket isimleri değişebilir.
    İlk çalıştırmada ham text'i görüp regex'i netleştiririz.
    """
    # Basit örnek: ₺12.345,67 veya 12345,67 gibi
    money = r"(?:₺\s*)?\d{1,3}(?:\.\d{3})*(?:,\d{2})"
    found = re.findall(money, html)
    return {
        "raw_found": found[:50],  # debug için ilk 50
    }

def fetch_ozbag_page() -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            locale="tr-TR",
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()
        page.goto(OZBAG_URL, wait_until="domcontentloaded", timeout=60000)

        # JS ile yüklenen içerik için biraz bekle:
        page.wait_for_timeout(4000)

        # Eğer sayfa üzerinde belli bir fiyat alanı varsa buraya selector koyarız:
        # page.wait_for_selector("text=Çeyrek", timeout=15000)

        html = page.content()
        browser.close()
        return html

@app.get("/")
def health():
    return {"ok": True, "service": "ozbag-scraper"}

@app.get("/ozbag")
def ozbag():
    html = fetch_ozbag_page()
    data = parse_prices_from_html(html)
    return {"source": OZBAG_URL, **data}
    from fastapi.responses import PlainTextResponse

@app.get("/prices.csv", response_class=PlainTextResponse)
def prices_csv():
    # Burada senin mevcut scrape/parse fonksiyonunu çağıracağız.
    # Örnek: result = get_prices() -> dict gibi
    result = get_prices()  # <-- senin fonksiyon adın neyse onu yaz

    # CSV formatı: başlık satırı + satırlar
    # Örnek dict: {"gram_altin": 1234.5, "ceyrek": 5678.0, ...}
    lines = ["kalem,fiyat"]
    for k, v in result.items():
        lines.append(f"{k},{v}")

    return "\n".join(lines)