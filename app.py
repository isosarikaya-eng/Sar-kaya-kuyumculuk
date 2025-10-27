# app.py — Sarıkaya Kuyumculuk • Harem Otomatik Entegrasyon (tam otomatik, marjlı)
import re, io, time
import pandas as pd
import datetime as dt
import streamlit as st
from sqlalchemy import create_engine, text

# ============== GENEL AYARLAR ==============
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Harem Otomatik", layout="wide")

DB_URL = "sqlite:///sarikkaya.db"
HAREM_URL = "https://www.haremaltin.com/"
AUTO_REFRESH_MS = 30_000  # 30 sn'de bir otomatik yenile

# Ürün tanımları (has hesabı için gerekirse)
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916,
                     "harem_alias": ["ESKİ ÇEYREK", "ESKI CEYREK", "ÇEYREK"]},
    "Yarım Altın" : {"unit": "adet", "std_weight": 3.50,  "purity": 0.916,
                     "harem_alias": ["ESKİ YARIM", "ESKI YARIM", "YARIM"]},
    "Tam Altın"   : {"unit": "adet", "std_weight": 7.00,  "purity": 0.916,
                     "harem_alias": ["ESKİ TAM", "ESKI TAM", "TAM"]},
    "Ata Lira"    : {"unit": "adet", "std_weight": 7.216, "purity": 0.916,
                     "harem_alias": ["ESKİ ATA", "ESKI ATA", "ATA"]},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995,
                     "harem_alias": ["GRAM ALTIN", "HAS ALTIN", "24 AYAR"]},
}

# Marj kuralları
MARGINS = {
    # Gram (Harem SATIŞ baz)
    "24 Ayar Gram": {"buy_from": "sell", "buy_delta": -20.0, "sell_from": "sell", "sell_delta": +10.0},
    # Eski sikkeler (Harem ALIŞ/ SATIŞ baz)
    "Çeyrek Altın": {"buy_from": "buy",  "buy_delta": -50.0,  "sell_from": "sell", "sell_delta": +50.0},
    "Yarım Altın" : {"buy_from": "buy",  "buy_delta": -100.0, "sell_from": "sell", "sell_delta": +100.0},
    "Tam Altın"   : {"buy_from": "buy",  "buy_delta": -200.0, "sell_from": "sell", "sell_delta": +200.0},
    "Ata Lira"    : {"buy_from": "buy",  "buy_delta": -200.0, "sell_from": "sell", "sell_delta": +200.0},
}

# ============== DB ==============
@st.cache_resource(show_spinner=False)
def get_engine():
    eng = create_engine(DB_URL, future=True)
    with eng.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS prices(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,      -- 'HAREM'
                name   TEXT,      -- Harem kart başlığı (Eski Çeyrek, Gram Altın...)
                buy    REAL,
                sell   REAL,
                ts     TEXT
            )
        """))
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                product TEXT,
                ttype TEXT,       -- Alış / Satış
                unit  TEXT,
                qty_or_gram REAL,
                unit_price REAL,
                total REAL,
                note TEXT
            )
        """))
    return eng

engine = get_engine()

def save_prices_if_changed(rows: list[dict]):
    """rows: [{'name': 'Eski Çeyrek', 'buy': 9500, 'sell': 9650}, ...]"""
    if not rows: return 0
    # Son kayda göre değişenleri yaz
    inserted = 0
    with engine.begin() as c:
        for r in rows:
            df = pd.read_sql(text("""
                SELECT buy, sell FROM prices
                WHERE source='HAREM' AND name=:n
                ORDER BY datetime(ts) DESC LIMIT 1
            """), c, params={"n": r["name"]})
            changed = True
            if not df.empty:
                last_buy, last_sell = float(df.iloc[0]["buy"]), float(df.iloc[0]["sell"])
                changed = not (abs(last_buy - r["buy"]) < 1e-6 and abs(last_sell - r["sell"]) < 1e-6)
            if changed:
                c.execute(text("""
                    INSERT INTO prices(source,name,buy,sell,ts)
                    VALUES('HAREM', :n, :b, :s, :ts)
                """), {"n": r["name"], "b": float(r["buy"]), "s": float(r["sell"]),
                       "ts": dt.datetime.utcnow().isoformat(timespec="seconds")})
                inserted += 1
    return inserted

def read_latest_harem_map() -> dict:
    """En güncel Harem kayıtlarını {ad: {'buy':..,'sell':..}} döndürür."""
    with engine.begin() as c:
        df = pd.read_sql(text("""
            SELECT name, buy, sell, MAX(ts) as ts
            FROM prices
            WHERE source='HAREM'
            GROUP BY name
        """), c)
    out = {}
    for _, r in df.iterrows():
        out[str(r["name"])] = {"buy": float(r["buy"]), "sell": float(r["sell"])}
    return out

# ============== HAREM ÇEKME ==============
def _normalize_num(s: str) -> float:
    s = re.sub(r"[^\d.,-]", "", s or "")
    if "." in s and "," in s:
        # son ayırıcıyı ondalık kabul et
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        else:
            # 12.345.678 -> 12345678
            if re.match(r"^\d{1,3}(\.\d{3})+(\.\d+)?$", s):
                s = s.replace(".", "")
    return float(s)

def _parse_cards(html: str) -> dict:
    """
    Harem ana sayfadaki kartlardan ESKİ ÇEYREK/GRAM ALTIN...” metinlerini ve
    yakınındaki alış/satış sayılarını çeker (heuristic).
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True).upper()
    found = {}
    # Her ürün için, sayfada geçen bloklardan iki sayı yakalamaya çalışalım.
    for prod, meta in PRODUCTS.items():
        aliases = meta["harem_alias"]
        if any(a in text for a in aliases):
            # Bu basitleştirilmiş yöntem tüm sayfayı tarar ve yakın sayı dizilerini alır.
            # Daha sağlamı: alias geçen bloklara göre local parse. (Gerektiğinde daraltırız.)
            nums = re.findall(r"[-+]?\d{1,3}(?:[.\s]?\d{3})*(?:[.,]\d+)?", text)
            # İlk iki makul sayı: alış, satış varsayımı
            vals = []
            for n in nums:
                try:
                    vals.append(_normalize_num(n))
                except:
                    pass
            # Eğer çok fazla sayı varsa, yine de ilk iki mantıklı değeri alıyoruz.
            if len(vals) >= 2:
                found[prod] = {"buy": float(vals[0]), "sell": float(vals[1])}
    return found

def fetch_harem_prices() -> dict:
    """
    1) Requests ile hızlı çek.
    2) Veri gelmezse (JS) Playwright ile render ederek DOM'dan oku.
    Dönen: {'Çeyrek Altın': {'buy':..,'sell':..}, ...}   (ürün isimleri bizim sözlükteki key'ler)
    """
    import requests
    try:
        r = requests.get(HAREM_URL, timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        quick = _parse_cards(r.text)
        if quick:
            return quick
    except Exception:
        pass

    # Playwright fallback
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent="Mozilla/5.0")
            page = ctx.new_page()
            page.goto(HAREM_URL, wait_until="domcontentloaded", timeout=15_000)
            page.wait_for_timeout(2500)  # JS fiyat yüklenmesi için kısa bekleme
            html = page.content()
            browser.close()
        slow = _parse_cards(html)
        return slow
    except Exception:
        return {}

# ============== MARJLI FİYAT HESABI ==============
def compute_our_prices(harem_map: dict) -> pd.DataFrame:
    """
    harem_map: {'Çeyrek Altın': {'buy':..,'sell':..}, ...}
    return DataFrame: product | harem_buy | harem_sell | our_buy | our_sell
    """
    rows = []
    for prod, meta in PRODUCTS.items():
        hm = harem_map.get(prod) or {}
        hb, hs = hm.get("buy"), hm.get("sell")
        rb = rs = None
        rule = MARGINS.get(prod)
        if rule and hb is not None and hs is not None:
            # alış
            ref_b = hb if rule["buy_from"] == "buy" else hs
            rb = ref_b + rule["buy_delta"]
            # satış
            ref_s = hb if rule["sell_from"] == "buy" else hs
            rs = ref_s + rule["sell_delta"]
        rows.append([prod, hb, hs, rb, rs])
    df = pd.DataFrame(rows, columns=["Ürün","Harem Alış","Harem Satış","Bizim Alış","Bizim Satış"])
    return df

# ============== TRANSACTIONS/ENVANTER ==============
def write_tx(product, ttype, qty, unit_price, note=""):
    unit = PRODUCTS[product]["unit"]
    total = qty * unit_price
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO transactions(date, product, ttype, unit, qty_or_gram, unit_price, total, note)
            VALUES(:d,:p,:t,:u,:q,:up,:tot,:n)
        """), {
            "d": dt.datetime.now().isoformat(timespec="seconds"),
            "p": product, "t": ttype, "u": unit,
            "q": float(qty), "up": float(unit_price), "tot": float(total), "n": note or ""
        })

def read_tx(limit=200):
    return pd.read_sql(text("""
        SELECT date, product, ttype, unit, qty_or_gram AS qty, unit_price, total, note
        FROM transactions
        ORDER BY datetime(date) DESC
        LIMIT :lim
    """), engine, params={"lim": limit})

def inventory_summary():
    df = read_tx(10_000)
    if df.empty:
        return pd.DataFrame(columns=["Ürün","Stok","Birim"])
    rows = []
    for prod, meta in PRODUCTS.items():
        unit = meta["unit"]
        x = df[df["product"] == prod]
        qty = x.apply(lambda r: r["qty"] if r["ttype"]=="Alış" else -r["qty"], axis=1).sum()
        rows.append([prod, round(qty,3), unit])
    return pd.DataFrame(rows, columns=["Ürün","Stok","Birim"])

def cash_summary():
    df = read_tx(10_000)
    if df.empty: return 0.0
    df["signed"] = df["total"] * df["ttype"].map({"Alış": -1, "Satış": +1})
    return float(df["signed"].sum())

# ============== OTO-REFRESH (her 30 sn) ==============
# Bu çağrı app'i periyodik yeniler; her yenilemede Harem çekimi tetiklenir.
st.autorefresh = st.experimental_rerun  # backward safety (eğer bazı ortamlar eskiyse)
try:
    from streamlit.runtime.scriptrunner.script_run_context import add_script_run_ctx  # no-op, sadece import guard
except:
    pass
st_autorefresh = st.experimental_rerun  # guard
try:
    from streamlit import runtime
    _ = st.runtime  # guard
except:
    pass
try:
    st.experimental_set_query_params(_=int(dt.datetime.utcnow().timestamp()))  # cache-breaker hint
except:
    pass
st.autorefresh_counter = st.session_state.get("autorefresh_counter_v1", 0)
st.session_state["autorefresh_counter_v1"] = st.autorefresh_counter + 1
st.experimental_singleton = None  # ensure no legacy caching issues

# Streamlit'in resmi fonksiyonu:
try:
    from streamlit import autorefresh as _st_autorefresh
except Exception:
    _st_autorefresh = None
if _st_autorefresh:
    _st_autorefresh(interval=AUTO_REFRESH_MS, key="auto_refresh_v1")

# ============== ANA AKIŞ: HAREM'İ OTOMATİK ÇEK & DB'YE YAZ ==============
with st.spinner("Harem fiyatları kontrol ediliyor..."):
    data = fetch_harem_prices()
    if data:
        # Harem ham verisini DB'ye (değiştiyse) yaz
        rows = []
        for prod, vals in data.items():
            # 'data' dict anahtarları bizim ürün adlarımız (Çeyrek Altın, 24 Ayar Gram...)
            # Harem adını da bizim anahtar ismi gibi saklıyoruz.
            rows.append({"name": prod, "buy": float(vals.get("buy", 0)), "sell": float(vals.get("sell", 0))})
        inserted = save_prices_if_changed(rows)
        if inserted > 0:
            st.toast(f"Harem güncellendi ({inserted} kalem).", icon="🔄")
    else:
        st.warning("Harem verisi alınamadı (ağ/JS engeli olabilir). Playwright kurulumu gerekli olabilir.")

# ============== UI ==============
st.title("💎 Sarıkaya Kuyumculuk — Otomatik Harem Entegrasyonu")

tabs = st.tabs(["📈 Canlı Fiyatlar", "💱 Alış / Satış", "🏦 Kasa & Envanter"])

# ---- Canlı Fiyatlar ----
with tabs[0]:
    st.subheader("Canlı Fiyatlar (Harem → Bizim Marj)")
    latest = read_latest_harem_map()
    df_live = compute_our_prices(latest)
    st.dataframe(df_live.style.format({
        "Harem Alış":"{:,.2f}", "Harem Satış":"{:,.2f}",
        "Bizim Alış":"{:,.2f}", "Bizim Satış":"{:,.2f}",
    }), use_container_width=True, height=320)

    st.caption(f"Son güncelleme: {dt.datetime.now().strftime('%H:%M:%S')} • Her {AUTO_REFRESH_MS//1000} sn’de otomatik yenilenir.")

# ---- Alış / Satış ----
with tabs[1]:
    st.subheader("Alış / Satış İşlemi (Öneri fiyat otomatik)")
    col1, col2, col3 = st.columns([2,2,2])
    with col1:
        product = st.selectbox("Ürün", list(PRODUCTS.keys()), key="tx_prod_v1")
    with col2:
        ttype = st.radio("Tür", ["Alış","Satış"], horizontal=True, key="tx_type_v1")
    with col3:
        unit = PRODUCTS[product]["unit"]
        step = 1.0 if unit=="adet" else 0.10
        qty = st.number_input("Miktar", min_value=0.01, value=1.0, step=step, key="tx_qty_v1")

    # Öneri fiyat: bizim marjlı tabloya göre
    row = df_live[df_live["Ürün"] == product]
    suggested = 0.0
    if not row.empty:
        suggested = float(row.iloc[0]["Bizim Alış" if ttype=="Alış" else "Bizim Satış"])
    st.markdown("##### Önerilen Birim Fiyat")
    st.markdown(f"<div style='font-size:28px;font-weight:700'>{suggested:,.2f} ₺</div>", unsafe_allow_html=True)

    manual = st.number_input("Birim Fiyat (manuel değiştirebilirsiniz)", value=float(round(suggested,2)), step=1.0, key="tx_price_v1")
    total = manual * qty
    st.success(f"Toplam: {total:,.2f} ₺")

    note = st.text_input("Not (opsiyonel)", key="tx_note_v1")
    if st.button("Kaydet", type="primary", key="tx_save_v1"):
        try:
            write_tx(product, ttype, qty, manual, note)
            st.success("İşlem kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son İşlemler")
    st.dataframe(read_tx(50).style.format({"qty":"{:,.3f}","unit_price":"{:,.2f}","total":"{:,.2f}"}),
                 use_container_width=True, height=320)

# ---- Kasa & Envanter ----
with tabs[2]:
    st.subheader("Kasa & Envanter")
    st.metric("Kasa Bakiyesi", f"{cash_summary():,.2f} ₺")
    st.markdown("### Envanter Özeti")
    st.dataframe(inventory_summary().style.format({"Stok":"{:,.3f}"}), use_container_width=True, height=300)