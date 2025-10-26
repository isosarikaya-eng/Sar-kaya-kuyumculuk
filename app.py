# app.py
# -*- coding: utf-8 -*-

import io
import datetime as dt
from typing import Optional, Dict, List

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy import Column, Integer, Float, String, Date, DateTime, Text, ForeignKey

# ==================== Veritabanı ====================
DB_URL = "sqlite:///sarikaya_kuyum.db"
engine = create_engine(DB_URL, echo=False, future=True)
Session = sessionmaker(bind=engine)
Base = declarative_base()

class Price(Base):
    __tablename__ = "prices"
    id = Column(Integer, primary_key=True)
    source = Column(String)        # "HAREM" | "OZBAG"
    name = Column(String)          # "Eski Çeyrek" | "Gram Altın" | ...
    buy = Column(Float, nullable=True)   # Harem için: alış TL; Özbağ için boş
    sell = Column(Float, nullable=True)  # Harem için: satış TL; Özbağ için boş
    has = Column(Float, nullable=True)   # Özbağ için: has çarpanı (örn 0.3520); Harem’de ops.
    ts = Column(DateTime, default=dt.datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    date = Column(Date, default=dt.date.today)
    product = Column(String)       # "Çeyrek Altın" vb.
    ttype = Column(String)         # "Alış" | "Satış"
    unit = Column(String)          # "adet" | "gram"
    qty_or_gram = Column(Float)    # adet veya gram
    price_tl = Column(Float)       # birim fiyat
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    product = Column(String, unique=True)    # ürün adı
    buy_add = Column(Float, default=0.0)     # öneri alışa eklenecek (TL)
    sell_add = Column(Float, default=0.0)    # öneri satışa eklenecek (TL)

Base.metadata.create_all(engine)

# ==================== Ürün Tanımları ====================
PRODUCTS: Dict[str, Dict] = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75,  "purity": 0.916},
    "Yarım Altın":  {"unit": "adet", "std_weight": 3.50,  "purity": 0.916},
    "Tam Altın":    {"unit": "adet", "std_weight": 7.00,  "purity": 0.916},
    "Ata Lira":     {"unit": "adet", "std_weight": 7.216, "purity": 0.916},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00,  "purity": 0.995},
}

# Harem'de aranan adlar (öncelik sırası)
HAREM_ALIASES: Dict[str, List[str]] = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın":  ["Eski Yarım", "Yarım"],
    "Tam Altın":    ["Eski Tam",   "Tam"],
    "Ata Lira":     ["Eski Ata",   "Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "24 Ayar Gram"],
}

# ==================== Yardımcılar ====================
def read_df(sql: str, params: dict = None) -> pd.DataFrame:
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})

def write_df(table: str, df: pd.DataFrame):
    with engine.begin() as conn:
        df.to_sql(table, conn, if_exists="append", index=False)

def get_price(source: str, name: str) -> Optional[pd.Series]:
    q = """
        SELECT * FROM prices
        WHERE source=:src AND name=:n
        ORDER BY ts DESC
        LIMIT 1
    """
    df = read_df(q, {"src": source, "n": name})
    if df.empty:
        return None
    return df.iloc[0]

def get_price_by_any(source: str, names: List[str], field: str) -> Optional[float]:
    for n in names:
        rec = get_price(source, n)
        if rec is not None and pd.notna(rec.get(field)):
            return float(rec[field])
    return None

def ensure_default_margins():
    """Varsayılan marj kayıtlarını 1 kez oluşturur."""
    sess = Session()
    try:
        for p in PRODUCTS.keys():
            if not sess.query(Setting).filter(Setting.product == p).first():
                # Varsayılanlar: gram için -20/+10; coinler için -50/+50
                if p == "24 Ayar Gram":
                    sess.add(Setting(product=p, buy_add= -20.0, sell_add= 10.0))
                else:
                    sess.add(Setting(product=p, buy_add= -50.0, sell_add= 50.0))
        sess.commit()
    finally:
        sess.close()

ensure_default_margins()

def get_margins() -> pd.DataFrame:
    return read_df("SELECT product, buy_add, sell_add FROM settings ORDER BY product")

def save_margin(product: str, buy_add: float, sell_add: float):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE settings SET buy_add=:b, sell_add=:s WHERE product=:p"),
            {"b": buy_add, "s": sell_add, "p": product},
        )

def suggested_price(product_name: str, ttype: str) -> Optional[float]:
    """
    Öneri fiyat mantığı:
    - 24 Ayar Gram: HAREM'de "Gram Altın" satışı baz; Alış = baz-20, Satış = baz+10 (sonra ürün marjı eklenir)
    - Çeyrek/Yarım/Tam/Ata: HAREM'de "Eski ..." satışı baz; Alış = baz + buy_add; Satış = baz + sell_add
      (varsayılan buy_add=-50, sell_add=+50)
    """
    aliases = HAREM_ALIASES.get(product_name, [product_name])

    # Baz satış fiyatı (HAREM)
    base_sell = get_price_by_any("HAREM", aliases, "sell")
    if base_sell is None:
        return None

    # Ürün marjları
    mg = read_df(
        "SELECT buy_add, sell_add FROM settings WHERE product=:p LIMIT 1",
        {"p": product_name},
    )
    buy_add = float(mg.iloc[0]["buy_add"]) if not mg.empty else 0.0
    sell_add = float(mg.iloc[0]["sell_add"]) if not mg.empty else 0.0

    # Gram altın özel kural
    if product_name == "24 Ayar Gram":
        buy_base = base_sell - 20.0
        sell_base = base_sell + 10.0
    else:
        buy_base = base_sell     # coin alış/satış ikisi de satış bazından gidecek; marjlarla ayrışır
        sell_base = base_sell

    if ttype == "Alış":
        return buy_base + buy_add
    else:
        return sell_base + sell_add

def fmt_tl(x: Optional[float]) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{x:,.0f} ₺".replace(",", ".")

# ==================== UI ====================
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="wide")
st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")

# ---- Sol Panel: Marj Ayarları
with st.sidebar:
    st.header("⚙️ Marj Ayarları")
    st.caption("Öneri hesaplarında kullanılacak TL marjlar.")
    mdf = get_margins()
    if not mdf.empty:
        for _, r in mdf.iterrows():
            c1, c2, c3 = st.columns([2,1,1])
            with c1:
                st.write(f"**{r['product']}**")
            with c2:
                nb = st.number_input(f"Alış marjı ({r['product']})", value=float(r["buy_add"]), step=5.0, key=f"b_{r['product']}")
            with c3:
                ns = st.number_input(f"Satış marjı ({r['product']})", value=float(r["sell_add"]), step=5.0, key=f"s_{r['product']}")
            if st.button(f"Kaydet ({r['product']})"):
                save_margin(r["product"], st.session_state[f"b_{r['product']}"], st.session_state[f"s_{r['product']}"])
                st.success("Kaydedildi.")

tabs = st.tabs([
    "Harem Fiyatları (Müşteri Bazı)",
    "İşlem (Alış/Satış)",
    "Özbağ Fiyatları (Has Referansı)",
    "Envanter Raporu",
])

# ==================== TAB 1: Harem ====================
with tabs[0]:
    st.subheader("Harem Fiyatları (Müşteri Bazı)")
    st.caption("CSV biçimi: **Ad,Alış,Satış**  | Örnekler: `Eski Çeyrek,9516,9644` • `Gram Altın,5836.65,5924.87`")
    h_txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="harem_csv")

    if st.button("Harem İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(h_txt), header=None, names=["name","buy","sell"])
            df["source"] = "HAREM"
            df["ts"] = dt.datetime.utcnow()
            write_df("prices", df[["source","name","buy","sell","ts"]])
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son Harem Fiyatları")
    h_last = read_df("""
        SELECT source, name, buy, sell, ts
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY source, name ORDER BY ts DESC) AS rn
            FROM prices WHERE source='HAREM'
        ) t WHERE rn=1
        ORDER BY name
    """)
    st.dataframe(h_last, use_container_width=True)

# ==================== TAB 2: İşlem (Alış/Satış) ====================
with tabs[1]:
    st.subheader("İşlem (Alış/Satış)")
    st.caption("Öneri fiyatı Harem'deki son kayda göre **10 sn** aralıkla otomatik güncellenir.")
    st.experimental_data_editor  # keeps mypy calm (noop)

    # Otomatik yenile (10 sn)
    st.experimental_rerun  # (guard against type-hints)
    st_autorefresh = st.experimental_data_editor  # placeholder keepers

    st_autorefresh = st.experimental_rerun  # silence linters
    st_autorefresh = st.autorefresh(interval=10_000, key="auto_r")

    colA, colB = st.columns([2,1])
    with colA:
        product = st.selectbox("Ürün", list(PRODUCTS.keys()))
    with colB:
        ttype = st.radio("Tür", ["Satış", "Alış"], horizontal=True)

    unit = PRODUCTS[product]["unit"]
    std_weight = PRODUCTS[product]["std_weight"]

    c1, c2 = st.columns(2)
    with c1:
        qty = st.number_input("Adet" if unit=="adet" else "Gram", min_value=0.01, value=1.00, step=1.0 if unit=="adet" else 0.1)
    with c2:
        note = st.text_input("Not", "")

    # Öneri fiyat
    sp = suggested_price(product, ttype)
    st.markdown("---")
    cL, cM, cR = st.columns([2,2,2])
    with cL:
        st.metric("Harem Baz (Satış) – bilgi", fmt_tl(get_price_by_any("HAREM", HAREM_ALIASES.get(product,[product]), "sell")))
    with cM:
        st.metric(f"Önerilen {ttype} Fiyat", fmt_tl(sp))
    with cR:
        st.caption("Marjlar sol panelden değiştirilebilir.")

    price_in = st.number_input("Birim Fiyat (TL)", value=float(sp or 0), step=10.0, format="%.2f")

    # Uyarılar
    if sp is not None:
        if ttype == "Satış" and price_in < sp:
            st.error("⚠️ Satış fiyatı önerinin ALTINDA. Lütfen kontrol edin.")
        if ttype == "Alış" and price_in > sp:
            st.error("⚠️ Alış fiyatı önerinin ÜSTÜNDE. Lütfen kontrol edin.")

    if st.button("Kaydet"):
        try:
            df = pd.DataFrame([{
                "date": dt.date.today(),
                "product": product,
                "ttype": ttype,
                "unit": unit,
                "qty_or_gram": float(qty),
                "price_tl": float(price_in),
                "note": note or "",
                "created_at": dt.datetime.utcnow(),
            }])
            write_df("transactions", df)
            st.success(f"{product} için {ttype} kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son İşlemler")
    tx = read_df("SELECT date, product, ttype, unit, qty_or_gram, price_tl, created_at FROM transactions ORDER BY created_at DESC LIMIT 50")
    st.dataframe(tx, use_container_width=True)

# ==================== TAB 3: Özbağ ====================
with tabs[2]:
    st.subheader("Özbağ Fiyatları (Toptancı / Has Referansı)")
    st.caption("CSV biçimi: **Ad,Has**  | Örnek: `Çeyrek,0.3520`  • `24 Ayar Gram,1.0000`")
    o_txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="ozbag_csv")

    if st.button("Özbağ İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(o_txt), header=None, names=["name","has"])
            df["source"] = "OZBAG"
            df["buy"] = None
            df["sell"] = None
            df["ts"] = dt.datetime.utcnow()
            write_df("prices", df[["source","name","buy","sell","has","ts"]])
            st.success("Özbağ kayıtları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son Özbağ Fiyatları")
    o_last = read_df("""
        SELECT source, name, has, ts
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY source, name ORDER BY ts DESC) AS rn
            FROM prices WHERE source='OZBAG'
        ) t WHERE rn=1
        ORDER BY name
    """)
    st.dataframe(o_last, use_container_width=True)

# ==================== TAB 4: Envanter ====================
with tabs[3]:
    st.subheader("📊 Envanter (Has Bazlı)")
    tx = read_df("SELECT * FROM transactions ORDER BY created_at DESC")
    if tx.empty:
        st.info("Henüz işlem yok. Lütfen **İşlem** sekmesinden alış/satış ekleyin.")
    else:
        # İşlemleri has (gr) cinsine çevir
        def calc_has(row):
            prod = row["product"]
            unit = row["unit"]
            qty = row["qty_or_gram"]
            purity = PRODUCTS[prod]["purity"]
            if unit == "adet":
                gram = qty * PRODUCTS[prod]["std_weight"]
            else:
                gram = qty
            return gram * purity

        tx["has_gr"] = tx.apply(calc_has, axis=1)
        total_has = tx["has_gr"].sum()
        st.metric("Toplam Has (gr)", f"{total_has:,.2f}".replace(",", "."))

        # Has karşılığı (TL) = toplam_has * Harem'de 24 Ayar Gram satışı
        gram_sell = get_price_by_any("HAREM", HAREM_ALIASES["24 Ayar Gram"], "sell")
        if gram_sell:
            st.metric("Has Karşılığı (TL) – Harem 24 Ayar Satış", fmt_tl(total_has * gram_sell))

        st.markdown("#### İşlem Listesi")
        st.dataframe(
            tx[["date","product","ttype","unit","qty_or_gram","price_tl","has_gr","created_at"]],
            use_container_width=True
        )