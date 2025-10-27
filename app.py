import re
import pandas as pd
import datetime as dt
from sqlalchemy import create_engine, text
import streamlit as st

# ================== SABİTLER ==================

DB_URL = "sqlite:///sarikkaya.db"

PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916},
    "Yarım Altın" : {"unit": "adet", "std_weight": 3.50,  "purity": 0.916},
    "Tam Altın"   : {"unit": "adet", "std_weight": 7.00,  "purity": 0.916},
    "Ata Lira"    : {"unit": "adet", "std_weight": 7.216, "purity": 0.916},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995},
}

# Harem isim eşleştirme
HAREM_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın" : ["Eski Yarım", "Yarım"],
    "Tam Altın"   : ["Eski Tam", "Tam"],
    "Ata Lira"    : ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "24 Ayar Gram"],
}

# Marj kuralları
GRAM_ALIS_DELTA  = -20.0
GRAM_SATIS_DELTA = +10.0

OLD_COIN_DELTAS = {
    "Çeyrek Altın": {"Alış": -50.0,  "Satış": +50.0},
    "Yarım Altın" : {"Alış": -100.0, "Satış": +100.0},
    "Tam Altın"   : {"Alış": -200.0, "Satış": +200.0},
    "Ata Lira"    : {"Alış": -200.0, "Satış": +200.0},
}

# ================== DB ==================

@st.cache_resource(show_spinner=False)
def get_engine():
    eng = create_engine(DB_URL, future=True)
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                name   TEXT,
                buy    REAL,
                sell   REAL,
                ts     TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                product TEXT,
                ttype TEXT,           -- Alış / Satış
                unit  TEXT,           -- adet / gram
                qty_or_gram REAL,
                unit_price REAL,
                total REAL,
                note  TEXT
            )
        """))
    return eng

engine = get_engine()

# ================== SAYI NORMALİZE ==================

def to_float_any(s: str) -> float:
    s0 = (s or "").strip()
    s1 = re.sub(r"[^\d.,\-]", "", s0)
    if "." in s1 and "," in s1:
        # son ayırıcıyı ondalık say
        if s1.rfind(",") > s1.rfind("."):
            s1 = s1.replace(".", "")
            s1 = s1.replace(",", ".")
        else:
            s1 = s1.replace(",", "")
    else:
        if "," in s1:
            s1 = s1.replace(".", "")
            s1 = s1.replace(",", ".")
        else:
            # yalnız noktalı binlikleri sil (12.345.678)
            if re.match(r"^\d{1,3}(\.\d{3})+(\.\d+)?$", s1):
                s1 = s1.replace(".", "")
    return float(s1)

# ================== HAREM CSV ==================

def parse_harem_csv(raw: str) -> pd.DataFrame:
    rows = []
    lines = (raw or "").splitlines()
    if not any(l.strip() for l in lines):
        raise ValueError("Boş veri. Biçim: Ad,Alış,Satış")
    for ln in lines:
        line = ln.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            raise ValueError(f"Satır hatalı: '{line}'. Biçim: Ad,Alış,Satış")
        name = parts[0]
        buy  = to_float_any(parts[1])
        sell = to_float_any(parts[2])
        rows.append((name, buy, sell))
    df = pd.DataFrame(rows, columns=["name", "buy", "sell"])
    df["source"] = "HAREM"
    df["ts"] = dt.datetime.utcnow().isoformat(timespec="seconds")
    return df[["source", "name", "buy", "sell", "ts"]]

def save_prices(df: pd.DataFrame):
    with engine.begin() as conn:
        df.to_sql("prices", conn, if_exists="append", index=False)

def read_harem_last(n=200) -> pd.DataFrame:
    q = """
    SELECT source, name, buy, sell, ts
    FROM prices
    WHERE source='HAREM'
    ORDER BY datetime(ts) DESC
    LIMIT :n
    """
    return pd.read_sql(text(q), engine, params={"n": n})

def get_last_harem_price_by_names(names, field="sell"):
    if not names:
        return None
    placeholders = ",".join([f":n{i}" for i in range(len(names))])
    params = {f"n{i}": nm for i, nm in enumerate(names)}
    q = f"""
    SELECT {field} AS v
    FROM prices
    WHERE source='HAREM' AND name IN ({placeholders})
    ORDER BY datetime(ts) DESC
    LIMIT 1
    """
    df = pd.read_sql(text(q), engine, params=params)
    if df.empty:
        return None
    return float(df.iloc[0]["v"])

# ================== İŞLEMLER & ENVANTER ==================

def write_tx(product, ttype, qty, unit_price, note=""):
    meta = PRODUCTS[product]
    unit = meta["unit"]
    total = (qty * unit_price) if unit_price is not None else None
    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO transactions(date, product, ttype, unit, qty_or_gram, unit_price, total, note)
            VALUES (:date,:product,:ttype,:unit,:qty,:price,:total,:note)
            """),
            {
                "date": dt.datetime.now().isoformat(timespec="seconds"),
                "product": product,
                "ttype": ttype,
                "unit": unit,
                "qty": float(qty),
                "price": None if unit_price is None else float(unit_price),
                "total": None if total is None else float(total),
                "note": note or ""
            }
        )

def read_tx(limit=200):
    q = """
    SELECT date, product, ttype, unit, qty_or_gram AS qty, unit_price, total, note
    FROM transactions
    ORDER BY datetime(date) DESC
    LIMIT :limit
    """
    return pd.read_sql(text(q), engine, params={"limit": limit})

def inventory_summary():
    df = read_tx(10_000)
    if df.empty:
        return pd.DataFrame(columns=["ürün","stok","birim","has(gr)"])
    rows = []
    for product, meta in PRODUCTS.items():
        unit = meta["unit"]
        pur  = meta["purity"]
        w    = meta["std_weight"]
        x = df[df["product"] == product]
        qty = x.apply(lambda r: r["qty"] if r["ttype"]=="Alış" else -r["qty"], axis=1).sum()
        has = qty * w * pur if unit=="adet" else qty * pur
        rows.append([product, round(qty, 3), unit, round(has, 3)])
    out = pd.DataFrame(rows, columns=["ürün","stok","birim","has(gr)"])
    out.loc["Toplam"] = ["—", "", "", round(out["has(gr)"].sum(), 3)]
    return out

def cash_summary():
    df = read_tx(10_000)
    if df.empty:
        return 0.0
    df = df.dropna(subset=["total"])
    sign = df["ttype"].map({"Alış": -1, "Satış": +1})
    return float((df["total"] * sign).sum())

# ================== ÖNERİ FİYAT ==================

def suggested_price(product: str, ttype: str):
    aliases = HAREM_ALIASES.get(product, [product])

    # 24 Ayar Gram: Harem SATIŞ baz alınır (alış/satış farkı delta ile)
    if product == "24 Ayar Gram":
        base_sell = get_last_harem_price_by_names(aliases, "sell")
        if base_sell is None:
            return None, "Harem'de Gram satırını bulamadım."
        if ttype == "Alış":
            val = base_sell + GRAM_ALIS_DELTA
            return val, f"Harem SATIŞ {base_sell:,.2f} + ({GRAM_ALIS_DELTA:+.0f})"
        else:
            val = base_sell + GRAM_SATIS_DELTA
            return val, f"Harem SATIŞ {base_sell:,.2f} + ({GRAM_SATIS_DELTA:+.0f})"

    # Eski çeyrek/yarım/tam/ata: marjlara göre
    if product in OLD_COIN_DELTAS:
        # Harem referansı: Alış için Harem ALIŞ, Satış için Harem SATIŞ
        field = "buy" if ttype == "Alış" else "sell"
        base = get_last_harem_price_by_names(aliases, field)
        if base is None:
            return None, f"Harem'de {aliases} bulunamadı."
        delta = OLD_COIN_DELTAS[product][ttype]
        return base + delta, f"Harem {field.upper()} {base:,.2f} + ({delta:+.0f})"

    # Fallback (gerekmesi halinde)
    field = "buy" if ttype == "Alış" else "sell"
    base = get_last_harem_price_by_names(aliases, field)
    if base is None:
        return None, f"Harem'de {aliases} bulunamadı."
    return base, f"Harem {field.upper()} {base:,.2f}"

# ================== UI ==================

st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="wide")

st.title("💎 Sarıkaya Kuyumculuk")
st.caption("– Entegrasyon")

tab1, tab2, tab3 = st.tabs(["📊 Harem Fiyatları", "💱 Alış / Satış", "🏦 Kasa & Envanter"])

# ----- TAB 1 -----
with tab1:
    st.subheader("Harem Fiyatları (CSV)")
    st.caption("Biçim: `Ad,Alış,Satış`  • Örnek: `Eski Çeyrek,9516.00,9644.00` veya `Gram Altın,5.724,20,5.825,00`")

    sample = (
        "Eski Çeyrek,9516.00,9644.00\n"
        "Eski Yarım,19032.00,19288.00\n"
        "Eski Tam,38064.00,38576.00\n"
        "Eski Ata,38300.00,38700.00\n"
        "Gram Altın,5.742,20,5.825,00"
    )
    h_txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="harem_csv_v4", value=sample)

    if st.button("Harem İçeri Al", type="primary", key="harem_btn_v4"):
        try:
            df = parse_harem_csv(h_txt)
            save_prices(df)
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son Harem Kayıtları")
    last = read_harem_last(200)
    st.dataframe(last.style.format({"buy":"{:,.0f}","sell":"{:,.0f}"}), use_container_width=True, height=360)

# ----- TAB 2 -----
with tab2:
    st.subheader("Alış / Satış İşlemi")
    st.caption("Öneri, Harem’deki **son kayıt**lara göre hesaplanır.")

    c1, c2, c3 = st.columns([2,2,2])
    with c1:
        product = st.selectbox("Ürün Seç", list(PRODUCTS.keys()), key="tx_prod_v4")
    with c2:
        ttype = st.radio("İşlem Türü", ["Alış", "Satış"], horizontal=True, key="tx_type_v4")
    with c3:
        unit = PRODUCTS[product]["unit"]
        step = 1.0 if unit=="adet" else 0.10
        qty  = st.number_input("Adet / Gram", min_value=0.01, value=1.0, step=step, key="tx_qty_v4")

    price, expl = suggested_price(product, ttype)
    st.markdown("##### Önerilen Fiyat")
    if price is None:
        st.warning(expl)
        price = 0.0
    st.markdown(f"<div style='font-size:28px;font-weight:700'>{price:,.2f} ₺</div>", unsafe_allow_html=True)
    st.caption(expl)

    st.markdown("##### Manuel Birim Fiyat (TL)")
    manual = st.number_input("Birim Fiyat (TL)", value=float(round(price,2)), step=1.0, key="tx_price_v4")

    # Uyarı (örnek kontrol – satış öneri altı)
    if ttype=="Satış" and manual < price:
        st.warning("⚠️ Satış fiyatı önerinin altında!")

    total = manual * qty
    st.success(f"Toplam: {total:,.2f} ₺")

    note = st.text_input("Not", key="tx_note_v4")
    if st.button("Kaydet", type="primary", key="tx_save_v4"):
        try:
            write_tx(product, ttype, qty, manual, note)
            st.success("İşlem kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son İşlemler")
    tx = read_tx(200)
    st.dataframe(tx.style.format({"qty":"{:,.3f}","unit_price":"{:,.2f}","total":"{:,.2f}"}), use_container_width=True, height=360)

# ----- TAB 3 -----
with tab3:
    st.subheader("Kasa & Envanter")
    inv = inventory_summary()
    st.markdown("### Envanter (Has Bazlı)")
    st.dataframe(inv, use_container_width=True)

    st.markdown("### Kasa (TL)")
    kasa = cash_summary()
    st.metric("Kasa Bakiyesi", f"{kasa:,.2f} ₺")

    st.markdown("### İşlemler (son 100)")
    tx2 = read_tx(100)
    st.dataframe(tx2.style.format({"qty":"{:,.3f}","unit_price":"{:,.2f}","total":"{:,.2f}"}), use_container_width=True, height=300)