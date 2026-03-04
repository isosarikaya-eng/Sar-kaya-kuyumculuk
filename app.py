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
    from fastapi.responses import HTMLResponse
from fastapi import Response
import json

@app.get("/tv", response_class=HTMLResponse)
def tv():
    return """
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fiyat Ekranı</title>
  <style>
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial;
           background:#0b0f14; color:#fff; }
    .wrap { height:100vh; display:flex; flex-direction:column; padding:4vh 5vw; gap:3vh; }
    .top { display:flex; justify-content:space-between; align-items:flex-end; opacity:.9; }
    .brand { font-size:4vh; font-weight:700; letter-spacing:.5px; }
    .time { font-size:3vh; opacity:.8; }
    .grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:2.5vw; flex:1; align-content:center; }
    .card { border:1px solid rgba(255,255,255,.10); border-radius:18px;
            padding:3.5vh 2.5vw; background:rgba(255,255,255,.04); }
    .label { font-size:4vh; opacity:.85; margin-bottom:2vh; }
    .price { font-size:8vh; font-weight:800; }
    .unit { font-size:3.5vh; opacity:.85; margin-left:.8vh; }
    .foot { font-size:2.6vh; opacity:.7; display:flex; justify-content:space-between; }
    .ok { color:#5CFF93; font-weight:700; }
    .bad { color:#FF5C5C; font-weight:700; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">SARIKAYA • FİYAT EKRANI</div>
      <div class="time" id="clock">--:--</div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="label">Çeyrek</div>
        <div class="price"><span id="ceyrek">-</span><span class="unit">₺</span></div>
      </div>
      <div class="card">
        <div class="label">Yarım</div>
        <div class="price"><span id="yarim">-</span><span class="unit">₺</span></div>
      </div>
      <div class="card">
        <div class="label">Tam</div>
        <div class="price"><span id="tam">-</span><span class="unit">₺</span></div>
      </div>
    </div>

    <div class="foot">
      <div>Kaynak: API /prices</div>
      <div>Durum: <span id="status" class="bad">Bağlanıyor...</span></div>
    </div>
  </div>

<script>
  const fmt = new Intl.NumberFormat('tr-TR');

  function setClock(){
    const d = new Date();
    const hh = String(d.getHours()).padStart(2,'0');
    const mm = String(d.getMinutes()).padStart(2,'0');
    document.getElementById("clock").textContent = `${hh}:${mm}`;
  }
  setClock(); setInterval(setClock, 1000);

  async function loadPrices(){
    try{
      const r = await fetch("/prices", { cache: "no-store" });
      if(!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();

      document.getElementById("ceyrek").textContent = fmt.format(data.ceyrek ?? data["Çeyrek"]);
      document.getElementById("yarim").textContent  = fmt.format(data.yarim  ?? data["Yarım"]);
      document.getElementById("tam").textContent    = fmt.format(data.tam    ?? data["Tam"]);

      document.getElementById("status").textContent = "Canlı";
      document.getElementById("status").className = "ok";
    }catch(e){
      document.getElementById("status").textContent = "Hata";
      document.getElementById("status").className = "bad";
    }
  }

  loadPrices();
  setInterval(loadPrices, 5000); // 5 sn'de bir yeniler
</script>
</body>
</html>
    """