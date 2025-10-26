# -- coding: utf-8 --
"""
Sarıkaya Kuyumculuk – Fiyat & Envanter Mini Uygulaması
- Harem (müşteri bazı) fiyatlarını CSV ile içeri alır
- Özbağ (toptancı/has referansı) verilerini CSV ile içeri alır
- Önerilen fiyatları kurala göre hesaplar
- (İsteğe bağlı) işlem kaydı ve envanter özeti

ÖNEMLİ KURALLAR (kullanıcı isteğine göre):
1) GRAM ALTIN (24 Ayar):
   - Harem "Gram Altın" SATIŞ fiyatı baz alınır.
   - Önerilen ALIŞ  = (Harem Gram SATIŞ) - 20 TL
   - Önerilen SATIŞ = (Harem Gram SATIŞ) + 10 TL

2) ÇEYREK / YARIM / TAM / ATA:
   - Harem'de “Eski Çeyrek / Eski Yarım / Eski Tam / Eski Ata” satırları baz alınır.
   - Önerilen ALIŞ  = Harem ALIŞ
   - Önerilen SATIŞ = Harem SATIŞ

CSV BEKLENEN FORMATLAR
- Harem: "Ad,Alış,Satış"   (Örn: Eski Çeyrek,9516,9644  | Gram Altın,5825,5910)
- Özbağ: "Ad,Has"          (Örn: Çeyrek,0.3520 | 24 Ayar Gram,1.0)
"""

import io
import datetime as dt
from typing import List, Optional

import pandas as pd
import streamlit as st
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Float, DateTime,
    select, desc, text
)

# ========================== VERİTABANI ==========================
DB_URL = "sqlite:///sarıkaya_kuyum.db"
engine = create_engine(DB_URL, future=True)
meta = MetaData()

prices = Table(
    "prices", meta,
    Column("id", Integer, primary_key=True),
    Column("source", String),    # "HAREM" / "OZBAG"
    Column("name", String),      # ürün adı (örn: Eski Çeyrek, Gram Altın, 24 Ayar Gram)
    Column("buy", Float),        # Harem için alış (TL) | Özbağ için boş
    Column("sell", Float),       # Harem için satış (TL) | Özbağ için boş
    Column("has", Float),        # Özbağ için has referansı (gr) | Harem için boş
    Column("ts", DateTime, default=dt.datetime.utcnow),
)

transactions = Table(
    "transactions", meta,
    Column("id", Integer, primary_key=True),
    Column("date", DateTime, default=dt.date.today),
    Column("product", String),           # Çeyrek Altın, Yarım Altın, Tam Altın, Ata Lira, 24 Ayar Gram
    Column("ttype", String),             # Alış / Satış
    Column("unit", String),              # adet / gram
    Column("qty_or_gram", Float),        # miktar ya da gram
    Column("unit_price_used", Float),    # kullanılan birim fiyat (TL)
    Column("amount", Float),             # toplam tutar (TL)
)

meta.create_all(engine)

# ========================== ÜRÜN SÖZLÜKLERİ ==========================
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet"},
    "Yarım Altın":  {"unit": "adet"},
    "Tam Altın":    {"unit": "adet"},
    "Ata Lira":     {"unit": "adet"},
    "24 Ayar Gram": {"unit": "gram"},
}

# Harem tarafı isim eşleştirmeleri (önce "Eski ..." aranacak)
HAREM_NAME_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın":  ["Eski Yarım", "Yarım"],
    "Tam Altın":    ["Eski Tam", "Tam"],
    "Ata Lira":     ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "24 Ayar Gram", "Has Altın"],
}

# ========================== DB YARDIMCILAR ==========================
def write_df(src: str, df: pd.DataFrame) -> None:
    """prices tablosuna DataFrame yazar (append)."""
    df = df.copy()
    df["source"] = src
    df["ts"] = dt.datetime.utcnow()
    with engine.begin() as conn:
        df.to_sql("prices", conn, if_exists="append", index=False)

def read_df(src: Optional[str] = None) -> pd.DataFrame:
    with engine.connect() as conn:
        if src:
            q = select(prices).where(prices.c.source == src).order_by(desc(prices.c.ts))
        else:
            q = select(prices).order_by(desc(prices.c.ts))
        rows = conn.execute(q).mappings().all()
    return pd.DataFrame(rows)

# IN (…) için güvenli sorgu
def get_price_by_any(src: str, names: List[str], field: str) -> Optional[float]:
    """Kaynak+isim eşlerinden en güncel kaydın field'ını döndürür."""
    if not names:
        return None
    _names = [n.strip() for n in names if n and n.strip()]
    if not _names:
        return None
    stmt = (
        select(prices.c[field])
        .where(prices.c.source == src)
        .where(prices.c.name.in_(_names))
        .order_by(desc(prices.c.ts))
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(stmt).first()
    return float(row[0]) if row else None

# ========================== KURAL MOTORU ==========================
def suggested_price(product: str, ttype: str) -> Optional[float]:
    """
    Kurallar:
      - 24 Ayar Gram → Harem 'Gram Altın' SATIŞ baz: ALIŞ = baz-20, SATIŞ = baz+10
      - Çeyrek/Yarım/Tam/Ata → Harem 'Eski …' satırları:
            ALIŞ  = Harem ALIŞ
            SATIŞ = Harem SATIŞ
    """
    aliases = HAREM_NAME_ALIASES.get(product, [product])

    if product == "24 Ayar Gram":
        base_sell = get_price_by_any("HAREM", aliases, "sell")
        if base_sell is None:
            return None
        return base_sell - 20.0 if ttype == "Alış" else base_sell + 10.0

    # Sikke ürünler: Eski … tercih
    h_buy = get_price_by_any("HAREM", aliases, "buy")
    h_sell = get_price_by_any("HAREM", aliases, "sell")
    if h_buy is None and h_sell is None:
        return None
    if ttype == "Alış":
        return h_buy if h_buy is not None else h_sell  # en azından biri gelsin
    else:
        return h_sell if h_sell is not None else h_buy

# ========================== UI ==========================
st.set_page_config(page_title="Sarıkaya Kuyumculuk", layout="wide")
st.title("💎 Sarıkaya Kuyumculuk — Entegrasyon")

page = st.sidebar.radio(
    "Menü",
    ["Fiyatlar (Harem & Özbağ)", "İşlem (Alış/Satış)", "Önerilen Fiyatlar"]
)

# -------- FİYATLAR ------------
if page == "Fiyatlar (Harem & Özbağ)":
    st.header("Harem Fiyatları (Müşteri Bazı)")
    st.caption("CSV biçimi: Ad,Alış,Satış  | Örnek: Eski Çeyrek,9516,9644  veya Gram Altın,5825,5910")
    h_txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="harem_csv")
    if st.button("Harem İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(h_txt), header=None)
            # Başlıksız da gelse 3 sütun beklenir → name,buy,sell
            if list(df.columns) == [0, 1, 2]:
                df.columns = ["name", "buy", "sell"]
            # Temizlik
            df["name"] = df["name"].astype(str).str.strip()
            df["buy"] = pd.to_numeric(df["buy"], errors="coerce")
            df["sell"] = pd.to_numeric(df["sell"], errors="coerce")
            df["has"] = None
            write_df("HAREM", df[["source","name","buy","sell","has","ts"]].assign(source="HAREM", ts=dt.datetime.utcnow()))
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.header("Özbağ Fiyatları (Toptancı / Has Referansı)")
    st.caption("CSV biçimi: Ad,Has  | Örnek: Çeyrek,0.3520  | 24 Ayar Gram için 1.0")
    o_txt = st.text_area("CSV'yi buraya yapıştırın", height=120, key="ozbag_csv")
    if st.button("Özbağ İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(o_txt), header=None)
            if list(df.columns) == [0, 1]:
                df.columns = ["name", "has"]
            df["name"] = df["name"].astype(str).str.strip()
            df["has"] = pd.to_numeric(df["has"], errors="coerce")
            df["buy"], df["sell"] = None, None
            write_df("OZBAG", df[["source","name","buy","sell","has","ts"]].assign(source="OZBAG", ts=dt.datetime.utcnow()))
            st.success("Özbağ referansları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Son Harem Fiyatları")
        dfh = read_df("HAREM")
        st.dataframe(dfh)
    with col2:
        st.subheader("Son Özbağ Fiyatları")
        dfo = read_df("OZBAG")
        st.dataframe(dfo)

# -------- İŞLEM ------------
elif page == "İşlem (Alış/Satış)":
    st.header("📦 İşlem Girişi")
    product = st.selectbox("Ürün", list(PRODUCTS.keys()))
    ttype = st.radio("Tür", ["Satış", "Alış"], horizontal=True)
    unit = PRODUCTS[product]["unit"]

    if unit == "adet":
        qty = st.number_input("Adet", min_value=1.0, value=1.0, step=1.0)
        qty_or_gram = qty
    else:
        gram = st.number_input("Gram", min_value=0.01, value=1.0, step=0.05)
        qty_or_gram = gram

    # Önerilen fiyatı kafadan getir
    suggested = suggested_price(product, ttype)
    unit_price = st.number_input(
        "Birim Fiyat (TL)",
        min_value=0.0,
        value=float(suggested or 0.0),
        step=1.0
    )
    if st.button("Kaydet"):
        amount = unit_price * qty_or_gram
        with engine.begin() as conn:
            conn.execute(transactions.insert().values(
                date=dt.date.today(),
                product=product,
                ttype=ttype,
                unit=unit,
                qty_or_gram=qty_or_gram,
                unit_price_used=unit_price,
                amount=amount,
            ))
        st.success(f"{product} için {ttype} kaydedildi. Tutar: {amount:,.0f}₺")

# -------- ÖNERİLEN FİYATLAR ------------
else:
    st.header("🧮 Önerilen Fiyatlar (Marj kurallarıyla)")

    # Harem tabanları
    rows = []
    for prod in ["Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira", "24 Ayar Gram"]:
        aliases = HAREM_NAME_ALIASES.get(prod, [prod])
        h_buy = get_price_by_any("HAREM", aliases, "buy")
        h_sell = get_price_by_any("HAREM", aliases, "sell")
        sug_buy = suggested_price(prod, "Alış")
        sug_sell = suggested_price(prod, "Satış")
        rows.append({
            "ürün": prod,
            "harem_alış": h_buy,
            "harem_satış": h_sell,
            "önerilen_alış": sug_buy,
            "önerilen_satış": sug_sell,
        })

    df_sug = pd.DataFrame(rows)
    st.dataframe(df_sug, use_container_width=True)
    st.caption("Not: Çeyrek/Yarım/Tam/Ata için Harem’de **Eski** satırları, Gram için **Gram Altın** satırları baz alınır. Gramda kural: alış = satış-20, satış = satış+10.")
