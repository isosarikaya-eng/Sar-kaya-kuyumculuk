# version 3

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.responses import JSONResponse

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
        "ceyrek": 12150,
        "yarim": 24300,
        "tam": 48600
    }
    return JSONResponse(content=data)


@app.get("/prices.csv", response_class=PlainTextResponse)
def prices_csv():
    csv = """kalem,fiyat
ceyrek,12150
yarim,24300
tam,48600
"""
    return csv