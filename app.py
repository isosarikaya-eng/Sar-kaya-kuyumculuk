# -- coding: utf-8 --

"""
Sarıkaya Kuyumculuk – Has Bazlı Envanter & Fiyat Entegrasyonu
Streamlit Cloud veya yerel ortamda çalışır.
Gereken paketler: streamlit, pandas
"""

import io
import sqlite3
import datetime as dt
import pandas as pd
import streamlit as st

DB_PATH = "sarıkaya_kuyum.db"

# ============== Yardımcılar: DB ==================
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices(
            source TEXT,  -- HAREM / OZBAG
            name   TEXT,
            buy    REAL,
            sell   REAL,
            ts     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions(
            date            TEXT,
            product         TEXT,  -- Çeyrek Altın vb
            ttype           TEXT,  -- Alış / Satış
            unit            TEXT,  -- adet / gram
            qty_or_gram     REAL,
            unit_price_used REAL,
            amount          REAL,
            has_grams       REAL,
            note            TEXT,
            created_at      TEXT
        )
    """)
    return conn

def write_df(table: str, df: pd.DataFrame):
    if df is None or df.empty:
        return
    conn = db()
    df.to_sql(table, conn, if_exists="append", index=False)
    conn.commit()

def read_sql(q: str, params: tuple = ()):
    conn = db()
    return pd.read_sql_query(q, conn, params=params)

def clear_source_in_prices(source: str):
    conn = db()
    conn.execute("DELETE FROM prices WHERE source=?", (source,))
    conn.commit()

def latest_prices(source: str) -> pd.DataFrame:
    """Kaydedilmiş son HAREM/OZBAG fiyat listesi (her adımdaki en son kayıtları döndürür)."""
    df = read_sql("SELECT * FROM prices WHERE source=? ORDER BY ts DESC", (source,))
    if df.empty:
        return df
    # aynı isimden birden çok kayıt varsa en son ts'li olanı al
    df = df.drop_duplicates(subset=["name"], keep="first")
    return df[["name", "buy", "sell", "ts"]].reset_index(drop=True)

# ============== Ürünler & Marjlar =================
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916, "sell_add": 50.0,  "buy_sub": 50.0},
    "Yarım Altın":  {"unit": "adet", "std_weight": 3.50,  "purity": 0.916, "sell_add": 100.0, "buy_sub": 100.0},
    "Tam Altın":    {"unit": "adet", "std_weight": 7.00,  "purity": 0.916, "sell_add": 200.0, "buy_sub": 200.0},
    "Ata Lira":     {"unit": "adet", "std_weight": 7.216, "purity": 0.916, "sell_add": 200.0, "buy_sub": 200.0},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995, "sell_add": 10.0,  "buy_sub": 20.0},
}

# Harem tarafındaki isimler için esnek eş-adlar
HAREM_NAME_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın":  ["Eski Yarım", "Yarım"],
    "Tam Altın":    ["Eski Tam", "Tam"],
    "Ata Lira":     ["Eski Ata", "Ata", "Ata Lira"],
    "24 Ayar Gram": ["Gram 24 Ayar", "24 Ayar Gram"],
}

def get_price_by_any(source: str, names: list[str], field: str = "sell") -> float | None:
    """Verilen isim adaylarından ilk bulunanın fiyatını getirir (HAREM/OZBAG)."""
    df = latest_prices(source)
    if df.empty:
        return None
    for nm in names:
        m = df[df["name"] == nm]
        if not m.empty:
            return float(m.iloc[0][field])
    return None


def suggested_price(product_name: str, ttype: str) -> float | None:
    """Önerilen kasa fiyatı: HAREM baz sell +/− marj."""
    aliases = HAREM_NAME_ALIASES.get(product_name, [product_name])
    base = get_price_by_any("HAREM", aliases, "sell")
    if base is None:
        return None
    if ttype == "Satış":
        return base + PRODUCTS[product_name]["sell_add"]
    else:
        return max(0.0, base - PRODUCTS[product_name]["buy_sub"])

# ============== UI =================
st.set_page_config(page_title="Sarıkaya Kuyumculuk", layout="wide")
st.title("💎 Sarıkaya Kuyumculuk – Envanter & Fiyat Entegrasyonu")

page = st.sidebar.radio("Menü", ["Fiyatlar (Özbağ & Harem)", "İşlem (Alış/Satış)", "Envanter Raporu"])

# ------------- FİYATLAR -------------
if page == "Fiyatlar (Özbağ & Harem)":
    st.subheader("Harem Fiyatları (Müşteri Bazı)")
    st.caption("CSV biçimi: Ad,Alış,Satış  |  Örnek: Çeyrek,0,3600")
    h_txt = st.text_area("CSV'yi buraya yapıştır", height=120, key="harem_csv")
    if st.button("Harem İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(h_txt), header=None)
            # Başlık yoksa 3 sütun bekliyoruz
            if df.shape[1] == 3:
                df.columns = ["name", "buy", "sell"]
            elif df.shape[1] == 2:
                df.columns = ["name", "sell"]  # alış verilmediyse 0 kabul
                df["buy"] = 0.0
                df = df[["name", "buy", "sell"]]
            else:
                raise ValueError("CSV 2 veya 3 sütun olmalı.")
            df["name"] = df["name"].astype(str).str.strip()
            df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0.0)
            df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0.0)
            df["source"] = "HAREM"
            df["ts"] = dt.datetime.utcnow().isoformat()
            clear_source_in_prices("HAREM")
            write_df("prices", df[["source", "name", "buy", "sell", "ts"]])
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.subheader("Özbağ Fiyatları (Toptancı / Has Maliyeti Referansı)")
    st.caption("CSV biçimi: Ad,Alış,Satış  |  Örnek: Çeyrek,0,3520  (Gram 24 Ayar için 24 ayar has TL/gr)")
    o_txt = st.text_area("CSV'yi buraya yapıştır", height=120, key="ozbag_csv")
    if st.button("Özbağ İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(o_txt), header=None)
            if df.shape[1] == 3:
                df.columns = ["name", "buy", "sell"]
            elif df.shape[1] == 2:
                df.columns = ["name", "sell"]
                df["buy"] = 0.0
                df = df[["name", "buy", "sell"]]
            else:
                raise ValueError("CSV 2 veya 3 sütun olmalı.")
            df["name"] = df["name"].astype(str).str.strip()
            df["buy"] = pd.to_numeric(df["buy"], errors="coerce").fillna(0.0)
            df["sell"] = pd.to_numeric(df["sell"], errors="coerce").fillna(0.0)
            df["source"] = "OZBAG"
            df["ts"] = dt.datetime.utcnow().isoformat()
            clear_source_in_prices("OZBAG")
            write_df("prices", df[["source", "name", "buy", "sell", "ts"]])
            st.success("Özbağ fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Son Harem Fiyatları")
        st.dataframe(latest_prices("HAREM"))
    with col2:
        st.markdown("#### Son Özbağ Fiyatları")
        st.dataframe(latest_prices("OZBAG"))

# ------------- İŞLEM -------------
elif page == "İşlem (Alış/Satış)":
    st.subheader("📦 İşlem Girişi")

    product_name = st.selectbox("Ürün", list(PRODUCTS.keys()))
    ttype = st.radio("Tür", ["Satış", "Alış"], horizontal=True)  # satış/alış sırası önemli değil
    unit = PRODUCTS[product_name]["unit"]

    if unit == "adet":
        qty = st.number_input("Adet", min_value=1.0, value=1.0, step=1.0)
        qty_or_gram = qty
        std_weight = PRODUCTS[product_name]["std_weight"]
        purity = PRODUCTS[product_name]["purity"]
        has_grams = qty * std_weight * purity
    else:
        gram = st.number_input("Gram", min_value=0.01, value=1.00, step=0.01, format="%.2f")
        qty_or_gram = gram
        purity = PRODUCTS[product_name]["purity"]
        has_grams = gram * purity

    # önerilen fiyat (HAREM baz +/− marj)
    s_price = suggested_price(product_name, ttype)
    price = st.number_input("Birim Fiyat (TL)", min_value=0.0, value=float(s_price or 0.0), step=1.0)
    note = st.text_input("Not", "")

    if st.button("Kaydet"):
        amount = qty_or_gram * price
        df = pd.DataFrame([{
            "date": dt.date.today().isoformat(),
            "product": product_name,
            "ttype": ttype,
            "unit": unit,
            "qty_or_gram": float(qty_or_gram),
            "unit_price_used": float(price),
            "amount": float(amount),
            "has_grams": float(has_grams if ttype == "Alış" else -has_grams),
            "note": note,
            "created_at": dt.datetime.utcnow().isoformat()
        }])
        write_df("transactions", df)
        st.success(f"{product_name} için {ttype} kaydedildi. (Has: {has_grams:.2f} gr, Tutar: {amount:,.0f}₺)")

    st.caption("Önerilen fiyatlar Harem satış fiyatına göre marj uygulanarak hesaplanır.")

# ------------- ENVANTER -------------
elif page == "Envanter Raporu":
    st.subheader("📊 Envanter (Has Bazlı)")

    tx = read_sql("SELECT * FROM transactions ORDER BY date DESC, created_at DESC")
    if tx.empty:
        st.info("Henüz işlem yok. Lütfen 'İşlem' sekmesinden alış/satış ekleyin.")
    else:
        total_has = float(tx["has_grams"].sum())
        # Özbağ 24 ayar has gr TL (sell) – ad eşleşmesi
        ozbag_24 = get_price_by_any("OZBAG", ["Gram 24 Ayar", "24 Ayar Gram"], "sell") or 0.0
        total_tl = total_has * ozbag_24

        m1, m2 = st.columns(2)
        with m1:
            st.metric("Toplam Has (gr)", f"{total_has:,.2f}")
        with m2:
            st.metric("Has Karşılığı (TL) – Özbağ 24 Ayar Satış", f"{total_tl:,.0f} ₺")

        st.dataframe(
            tx[["date", "product", "ttype", "unit", "qty_or_gram", "unit_price_used", "amount", "has_grams", "note"]]
        )
