from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
import requests

app = FastAPI()

@app.get("/")
def health():
    return {
        "ok": True,
        "service": "ozbag-scraper",
        "cache_ttl_seconds": 60
    }

@app.get("/prices")
def prices():
    data = {
        "Çeyrek": 12150,
        "Yarım": 24300,
        "Tam": 48600
    }
    return data


@app.get("/prices.csv", response_class=PlainTextResponse)
def prices_csv():
    csv = """kalem,fiyat
Çeyrek,12150
Yarım,24300
Tam,48600
"""
    return csv