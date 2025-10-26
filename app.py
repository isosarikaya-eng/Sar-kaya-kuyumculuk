# -*- coding: utf-8 -*-
import io
import datetime as dt
from typing import List, Dict, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

# ------------------ Genel ------------------
st.set_page_config(page_title="Sarıkaya Kuyumculuk", layout="wide")
DB_URL = "sqlite:///data.db"
engine = create_engine(DB_URL, future=True)

# Tablo oluştur
with engine.begin() as conn:
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS prices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT,   -- HAREM / OZBAG
      name   TEXT,   -- Çeyrek / Eski Çeyrek / Gram Altın ...
      buy    REAL,   -- Alış
      sell   REAL,   -- Satış
      ts     TEXT    -- ISO tarih
    );
    """)
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS transactions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      date TEXT,
      product TEXT,      -- Ürün adı (sabit sözlükten)
      ttype TEXT,        -- Alış / Satış
      unit  TEXT,        -- adet / gram
      qty_or_gram REAL,  -- girilen miktar
      has_grams  REAL,   -- ürüne göre hesaplanan has
      unit_price REAL,   -- seçilen birim fiyat (TL)
      note TEXT,
      created_at TEXT
    );
    """)

def read_sql(sql: str, **kwargs) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, **kwargs)

def write_df(tablename: str, df: pd.DataFrame):
    with engine.begin() as conn:
        df.to_sql(tablename, conn, if_exists="append", index=False)

# ------------------ Ürün tanımları ------------------
# std_weight = tek parça ağırlığı (gr), purity = has oranı
PRODUCTS: Dict[str, Dict] = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916, "sale_add": 50,  "buy_add": -50},
    "Yarım Altın" : {"unit": "adet", "std_weight": 3.50,  "purity": 0.916, "sale_add": 100, "buy_add": -100},
    "Tam Altın"   : {"unit": "adet", "std_weight": 7.00,  "purity": 0.916, "sale_add": 200, "buy_add": -200},
    "Ata Lira"    : {"unit": "adet", "std_weight": 7.216, "purity": 0.916, "sale_add": 200, "buy_add": -200},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995, "sale_add": 10,  "buy_add": -20},  # gram için kural
}

# Harem tarafındaki isim eşleştirmeleri
HAREM_NAME_ALIASES: Dict[str, List[str]] = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın" : ["Eski Yarım", "Yarım"],
    "Tam Altın"   : ["Eski Tam", "Tam"],
    "Ata Lira"    : ["Ata", "Ata Lira"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "24 Ayar", "24 Ayar Gram"],
}

# ------------------ Yardımcılar ------------------
def tidy_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip() for c in df.columns]
    return df

def read_csv_textarea(txt: str, expected_cols=3) -> pd.DataFrame:
    if not txt.strip():
        return pd.DataFrame(columns=["name","buy","sell"])
    df = pd.read_csv(io.StringIO(txt.strip()), header=None)
    # Başlıksız ise: Ad, Alış, Satış
    if df.shape[1] == expected_cols:
        df.columns = ["name", "buy", "sell"]
    df = tidy_cols(df)
    # Sayısalları çevir
    for c in ["buy","sell"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def get_price_by_any(source: str, names: List[str], field: str) -> Optional[float]:
    # Son ts’a göre en günceli al
    q = """
        SELECT * FROM prices
        WHERE source = :src
          AND name IN :names
        ORDER BY ts DESC, id DESC
        LIMIT 1
    """
    with engine.connect() as conn:
        res = conn.execute(text(q), {"src": source, "names": tuple(names)}).mappings().all()
    if not res:
        return None
    return float(res[0][field])

def last_prices_df(source: str) -> pd.DataFrame:
    q = """
    SELECT p1.*
    FROM prices p1
    JOIN (
      SELECT name, MAX(ts) AS mx
      FROM prices WHERE source=:src GROUP BY name
    ) t
    ON p1.name=t.name AND p1.ts=t.mx
    WHERE p1.source=:src
    ORDER BY p1.name
    """
    return read_sql(q, params={"src": source})

def suggested_price(product_name: str, ttype: str) -> Optional[float]:
    """Öneri fiyatı hesaplar"""
    aliases = HAREM_NAME_ALIASES.get(product_name, [product_name])
    base_sell = get_price_by_any("HAREM", aliases, "sell")  # Harem satış baz
    if base_sell is None:
        return None

    if product_name == "24 Ayar Gram":
        # Özel kural: alış = satış-20, satış = satış+10
        if ttype == "Alış":
            return base_sell - 20
        else:
            return base_sell + 10

    # Sikke türleri için ön tanımlı marj
    sale_add = PRODUCTS[product_name]["sale_add"]
    buy_add  = PRODUCTS[product_name]["buy_add"]
    return base_sell + (sale_add if ttype == "Satış" else buy_add)

def calc_has(product_name: str, qty_or_gram: float) -> float:
    info = PRODUCTS[product_name]
    if info["unit"] == "adet":
        total_weight = qty_or_gram * info["std_weight"]
    else:
        total_weight = qty_or_gram  # gram girdisi
    return round(total_weight * info["purity"], 3)

# ------------------ UI ------------------
st.title("💎 Sarıkaya Kuyumculuk – Envanter & Fiyat Entegrasyonu")

menu = st.sidebar.radio("Menü", ["Fiyatlar (Özbağ & Harem)", "İşlem (Alış/Satış)", "Envanter Raporu"])

# -------- FİYATLAR --------
if menu == "Fiyatlar (Özbağ & Harem)":
    st.subheader("Özbağ Fiyatları (Toptancı / Has Maliyeti Referansı)")
    st.caption("CSV biçimi: Ad,Alış,Satış  |  Örnek: Çeyrek,0,3520  (Gram 24 Ayar için 24 ayar TL/gr)")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**CSV'yi buraya yapıştırın**")
        oz_txt = st.text_area("", height=140, key="oz_csv")
        if st.button("Özbağ İçeri Al"):
            try:
                df = read_csv_textarea(oz_txt)
                df["source"] = "OZBAG"
                df["ts"] = dt.datetime.utcnow().isoformat()
                write_df("prices", df[["source","name","buy","sell","ts"]])
                st.success("Özbağ fiyatları kaydedildi.")
            except Exception as e:
                st.error(f"Hata: {e}")

    st.subheader("Harem Fiyatları (Müşteri Bazı)")
    with col2:
        prod = st.selectbox("Ürün", list(PRODUCTS.keys()), index=4)
        typ  = st.radio("Tür", ["Satış","Alış"], horizontal=True)
        gram = st.number_input("Gram", value=1.0, min_value=0.01, step=0.01)
        unit_price = st.number_input("Birim Fiyat (TL)", value=0.0, step=1.0)
        if st.button("Kaydet"):
            name_for_store = prod
            # Gram için Harem’de genellikle “Gram Altın” adı geçer; yine de seçilen ürün adıyla saklıyoruz.
            row = pd.DataFrame([{
                "source": "HAREM",
                "name": name_for_store if prod != "24 Ayar Gram" else "Gram Altın",
                "buy": unit_price if typ == "Alış" else 0.0,
                "sell": unit_price if typ == "Satış" else 0.0,
                "ts": dt.datetime.utcnow().isoformat()
            }])
            write_df("prices", row)
            st.success("Harem fiyatı kaydedildi.")

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Son Harem Fiyatları")
        dfh = last_prices_df("HAREM")
        st.dataframe(dfh, use_container_width=True)
    with c2:
        st.caption("Son Özbağ Fiyatları")
        dfo = last_prices_df("OZBAG")
        st.dataframe(dfo, use_container_width=True)

    st.markdown("### Önerilen Fiyatlar (Marj kurallarıyla)")
    rows = []
    for p in PRODUCTS.keys():
        h_sell = get_price_by_any("HAREM", HAREM_NAME_ALIASES.get(p, [p]), "sell")
        h_buy  = get_price_by_any("HAREM", HAREM_NAME_ALIASES.get(p, [p]), "buy")
        rec_s  = suggested_price(p, "Satış")
        rec_b  = suggested_price(p, "Alış")
        rows.append({"ürün": p, "harem_satış": h_sell, "harem_alış": h_buy, "önerilen_satış": rec_s, "önerilen_alış": rec_b})
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
    st.caption("Not: Öneri hesabında Harem’de **Eski Çeyrek/Yarım/Tam/Ata** ve **Gram Altın (24 Ayar)** satırları baz alınır.")

# -------- İŞLEM --------
elif menu == "İşlem (Alış/Satış)":
    st.subheader("📦 İşlem Girişi")
    product = st.selectbox("Ürün", list(PRODUCTS.keys()))
    ttype   = st.radio("Tür", ["Satış","Alış"], horizontal=True)
    unit    = PRODUCTS[product]["unit"]

    if unit == "adet":
        qty = st.number_input("Adet", min_value=1.0, step=1.0, value=1.0)
        qty_or_gram = qty
    else:
        gram = st.number_input("Gram", min_value=0.01, step=0.01, value=1.0)
        qty_or_gram = gram

    suggested = suggested_price(product, ttype) or 0.0
    price = st.number_input("Birim Fiyat (TL)", value=float(suggested), step=1.0)
    note  = st.text_input("Not", "")

    if st.button("Kaydet"):
        has_g = calc_has(product, qty_or_gram)
        tx = pd.DataFrame([{
            "date": dt.date.today().isoformat(),
            "product": product,
            "ttype": ttype,
            "unit": unit,
            "qty_or_gram": qty_or_gram,
            "has_grams": has_g,
            "unit_price": price,
            "note": note,
            "created_at": dt.datetime.utcnow().isoformat(),
        }])
        write_df("transactions", tx)
        st.success(f"{product} için {ttype} kaydedildi. (Has: {has_g} gr)")

# -------- ENVANTER --------
else:
    st.subheader("📊 Envanter (Has Bazlı)")
    tx = read_sql("SELECT * FROM transactions ORDER BY created_at DESC")
    if tx.empty:
        st.info("Henüz işlem yok. Lütfen 'İşlem' sekmesinden alış/satış ekleyin.")
    else:
        # Toplam has: Alış (+), Satış (-)
        tx["signed_has"] = tx.apply(lambda r: r["has_grams"] if r["ttype"]=="Alış" else -r["has_grams"], axis=1)
        total_has = round(tx["signed_has"].sum(), 3)
        st.metric("Toplam Has (gr)", total_has)

        st.caption("İşlem detayları")
        st.dataframe(tx[["date","product","ttype","unit","qty_or_gram","has_grams","unit_price","note"]], use_container_width=True)

        # TL karşılığı için Özbağ 24 Ayar satış
        oz_sell = get_price_by_any("OZBAG", ["24 Ayar Gram","Gram 24 Ayar","Gram Altın","Has Altın","24 Ayar"], "sell")
        if oz_sell is not None:
            tl_value = round(total_has * oz_sell, 2)
            st.metric("Has Karşılığı (TL) – Özbağ 24 Ayar Satış", f"{tl_value:,.0f} ₺")
        else:
            st.warning("Özbağ 24 Ayar satış fiyatı bulunamadı. Fiyatlar sekmesinden ekleyin.")
