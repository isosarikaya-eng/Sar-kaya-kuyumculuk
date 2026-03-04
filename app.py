from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse

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
    return JSONResponse(content={
        "ceyrek": 12150,
        "yarim": 24300,
        "tam": 48600
    })


@app.get("/prices.csv", response_class=PlainTextResponse)
def prices_csv():
    csv = """kalem,fiyat
ceyrek,12150
yarim,24300
tam,48600
"""
    return csv


@app.get("/tv", response_class=HTMLResponse)
def tv():
    return """
<html>
<head>
<meta charset="utf-8">
<style>
body{
background:black;
color:white;
font-family:Arial;
text-align:center;
margin-top:100px;
}
.price{
font-size:80px;
margin:40px;
}
</style>
</head>

<body>

<h1>SARIKAYA KUYUMCULUK</h1>

<div class="price">Çeyrek: <span id="c">-</span> ₺</div>
<div class="price">Yarım: <span id="y">-</span> ₺</div>
<div class="price">Tam: <span id="t">-</span> ₺</div>

<script>
async function load(){
let r = await fetch("/prices");
let d = await r.json();

document.getElementById("c").innerText = d.ceyrek;
document.getElementById("y").innerText = d.yarim;
document.getElementById("t").innerText = d.tam;
}

load();
setInterval(load,5000);
</script>

</body>
</html>
"""