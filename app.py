# app.py
# Sarıkaya Kuyumculuk – Entegrasyon (baştan yazım)
# Streamlit 1.38+ uyumlu: st.data_editor ve st.rerun kullanır.

import io
import time
import datetime as dt
import pandas as pd
import numpy as np
import streamlit as st
from sqlalchemy import create_engine, text

# ---------------------- GENEL AYAR ----------------------
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon",
                   page_icon="💎",
                   layout="wide")

DB_URL = "sqlite:///data.db"
engine = create_engine(DB_URL, future=True)

# Ürün tanımı (standart ağırlık ve saflık yalnızca has hesabında kullanılır)
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet"},
    "Yarım Altın":   {"unit": "adet"},
    "Tam Altın":     {"unit": "adet"},
    "Ata Lira":      {"unit": "adet"},
    "24 Ayar Gram":  {"unit": "gram"},
    "22 Ayar Gram":  {"unit": "gram"},
    "22 Ayar 0,5 gr": {"unit": "adet"},
    "22 Ayar 0,25 gr":{"unit": "adet"},
}

# Harem tarafındaki isimler için esnek eşleştirme (öncelik sırası korunur)
HAREM_NAME_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın":  ["Eski Yarım",  "Yarım"],
    "Tam Altın":    ["Eski Tam",    "Tam"],
    "Ata Lira":     ["Eski Ata",    "Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "24 Ayar Gram"],
    # Diğerleri Harem’den baz alınmıyor (müşteri bazlı manuel fiyatlanır)
}

# Özbağ has çarpanları için varsayılanlar (CSV ile güncellenecek)
DEFAULT_OZBAG_HAS = {
    "Çeyrek Altın": 0.3520,
    "Yarım Altın":  0.7040,
    "Tam Altın":    1.4080,
    "Ata Lira":     1.4160,
    "24 Ayar Gram": 1.0000,
}

# ---------------------- DB YARDIMCI ----------------------
def init_db():
    with engine.begin() as conn:
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS prices (
            source TEXT,         -- 'HAREM' veya 'OZBAG'
            name   TEXT,
            buy    REAL,         -- Harem için alış, Özbağ için opsiyonel
            sell   REAL,         -- Harem için satış
            has    REAL,         -- Özbağ has çarpanı (örn 0.3520)
            ts     TIMESTAMP
        );
        """)
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS transactions (
            ts      TIMESTAMP,
            product TEXT,
            ttype   TEXT,     -- 'Alış' / 'Satış'
            unit    TEXT,     -- adet / gram
            qty     REAL,     -- adet veya gram
            price   REAL,     -- birim fiyat (TL)
            total   REAL,     -- qty * price (Alış için - toplam çıkış; Satış için + giriş)
            note    TEXT
        );
        """)
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS ozbag_cari (
            ts      TIMESTAMP,
            item    TEXT,     -- açıklama
            has     REAL,     -- + borç (has), - ödeme (has)
            note    TEXT
        );
        """)

@st.cache_data(ttl=5.0, show_spinner=False)
def read_sql(table: str) -> pd.DataFrame:
    try:
        return pd.read_sql_table(table, engine).sort_values("ts", ascending=False)
    except Exception:
        return pd.DataFrame()

def write_df(table: str, df: pd.DataFrame):
    if df.empty:
        return
    df.to_sql(table, engine, if_exists="append", index=False)

def upsert_prices(rows: pd.DataFrame):
    """Aynı (source,name) için son kaydı koruyarak toplu ekler (append modeli)."""
    rows = rows.copy()
    rows["ts"] = dt.datetime.utcnow()
    write_df("prices", rows)

# ---------------------- ORTAK YARDIMCILAR ----------------------
def to_float(s: str) -> float:
    # "5.836,65" -> 5836.65 gibi Türkçe girdileri yakalar
    if isinstance(s, (int, float, np.floating)):
        return float(s)
    s = str(s).strip().replace(" ", "")
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return np.nan

def parse_csv_lines(txt: str, expect_cols=3):
    """
    Basit CSV: name,buy,sell  veya name,has   (virgül ayraçlı; ondalık virgül destekli)
    """
    records = []
    for raw in txt.strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        # kalan sütunları doldur
        while len(parts) < expect_cols:
            parts.append("")
        records.append(parts[:expect_cols])
    return records

def latest_price(source: str, names: list[str], field: str) -> float | None:
    """Belirtilen kaynaktan listedeki ilk eşleşme için alanı döndürür."""
    df = read_sql("prices")
    if df.empty:
        return None
    df = df[df["source"] == source]
    # En yeni kayıtlar üstte; ilk eşleşmeyi döndür
    for nm in names:
        m = df[df["name"].str.lower() == nm.lower()]
        if not m.empty and field in m.columns and pd.notnull(m.iloc[0][field]):
            try:
                return float(m.iloc[0][field])
            except Exception:
                pass
    return None

def ozbag_has_map() -> dict:
    df = read_sql("prices")
    cmap = DEFAULT_OZBAG_HAS.copy()
    if df.empty:
        return cmap
    df = df[(df["source"] == "OZBAG") & df["name"].notna() & df["has"].notna()]
    for _, r in df.groupby("name").head(1).iterrows():
        cmap[r["name"]] = float(r["has"])
    return cmap

def suggested_price(product: str, ttype: str,
                    gram_buy_offset: float, gram_sell_offset: float,
                    coin_buy_offset: float, coin_sell_offset: float) -> float | None:
    """
    Dinamik öneri:
      - 24 Ayar Gram: HAREM 'Gram Altın' satışını baz alır.
        Alış: base_sell - gram_buy_offset   (default 20)
        Satış: base_sell + gram_sell_offset (default 10)
      - Çeyrek/Yarım/Tam/Ata: HAREM 'Eski ...' satışını baz alır.
        Alış: base_sell - coin_buy_offset
        Satış: base_sell + coin_sell_offset
      - Diğer ürünler: None
    """
    if product in ["Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira", "24 Ayar Gram"]:
        alias = HAREM_NAME_ALIASES.get(product, [product])
        base_sell = latest_price("HAREM", alias, "sell")
        if base_sell is None:
            return None
        if product == "24 Ayar Gram":
            return (base_sell - gram_buy_offset) if ttype == "Alış" else (base_sell + gram_sell_offset)
        else:
            return (base_sell - coin_buy_offset) if ttype == "Alış" else (base_sell + coin_sell_offset)
    return None

def auto_refresh(seconds: int = 10):
    key = "_last_refresh_ts"
    now = time.time()
    if key not in st.session_state:
        st.session_state[key] = now
    elif now - st.session_state[key] >= seconds:
        st.session_state[key] = now
        st.rerun()

# ---------------------- SIDEBAR ----------------------
with st.sidebar:
    st.markdown("### ⚙️ Marj Ayarları")
    col_a, col_b = st.columns(2)
    gram_buy_offset  = col_a.number_input("Gram Alış Offset (₺)", value=20.0, step=1.0)
    gram_sell_offset = col_b.number_input("Gram Satış Offset (₺)", value=10.0, step=1.0)

    col_c, col_d = st.columns(2)
    coin_buy_offset  = col_c.number_input("Çeyrek/Yarım/Tam/Ata Alış Offset (₺)", value=50.0, step=1.0)
    coin_sell_offset = col_d.number_input("… Satış Offset (₺)", value=50.0, step=1.0)

    st.markdown("---")
    page = st.radio("Menü", [
        "Harem Fiyatları (Müşteri Bazı)",
        "İşlem (Alış/Satış)",
        "Özbağ Fiyatları (Has Referansı)",
        "Envanter & Kasa",
        "Özbağ Cari (Has)"
    ])

st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")

init_db()  # tabloları hazırla

# ---------------------- HAREM ----------------------
if page == "Harem Fiyatları (Müşteri Bazı)":
    st.subheader("Harem Fiyatları (Müşteri Bazı)")
    st.caption("CSV biçimi: Ad,Alış,Satış  | Örnek: Eski Çeyrek,9516,9644  /  Gram Altın,5836.65,5924.87")

    txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="harem_csv")
    if st.button("Harem İçeri Al"):
        try:
            rows = parse_csv_lines(txt, expect_cols=3)
            data = []
            for name, b, s in rows:
                data.append({
                    "source": "HAREM",
                    "name": name,
                    "buy": to_float(b),
                    "sell": to_float(s),
                    "has": None,
                    "ts": dt.datetime.utcnow()
                })
            upsert_prices(pd.DataFrame(data))
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son Harem Kayıtları")
    hdf = read_sql("prices")
    hdf = hdf[hdf["source"] == "HAREM"][["source","name","buy","sell","ts"]]
    st.data_editor(hdf, use_container_width=True, disabled=True)

# ---------------------- İŞLEM ----------------------
elif page == "İşlem (Alış/Satış)":
    auto_refresh(10)  # Harem güncellemesine göre öneri 10 sn’de bir tazelensin
    st.subheader("İşlem (Alış/Satış)")
    st.caption("Öneri fiyatı Harem’deki son kayda göre 10 sn aralıkla otomatik güncellenir.")

    product = st.selectbox("Ürün", list(PRODUCTS.keys()))
    ttype   = st.radio("Tür", ["Satış", "Alış"], horizontal=True, index=1)
    unit    = PRODUCTS[product]["unit"]

    # adet/gram giriş
    if unit == "adet":
        qty = st.number_input("Adet", min_value=1.0, step=1.0, value=1.0)
    else:
        qty = st.number_input("Gram", min_value=0.01, step=0.01, value=1.00, format="%.2f")

    # Dinamik öneri
    suggested = suggested_price(product, ttype,
                                gram_buy_offset, gram_sell_offset,
                                coin_buy_offset, coin_sell_offset)
    col1, col2 = st.columns([2, 1])
    with col1:
        price = st.number_input("Birim Fiyat (TL)", min_value=0.0,
                                value=float(suggested) if suggested else 0.0,
                                step=1.0)
    with col2:
        st.metric("Öneri", f"{suggested:,.0f} ₺" if suggested else "—")

    note = st.text_input("Not", "")

    # Uyarı: satış önerinin altında ise veya alış önerinin üstünde ise
    if suggested is not None:
        if ttype == "Satış" and price < suggested:
            st.warning("⚠️ Öneri fiyatının **altında** satış yapıyorsunuz.")
        if ttype == "Alış" and price > suggested:
            st.warning("⚠️ Öneri fiyatının **üstünde** alış yapıyorsunuz.")

    if st.button("Kaydet"):
        try:
            sign = -1 if ttype == "Alış" else 1  # kasa bakiyesi için
            total = sign * qty * price
            df = pd.DataFrame([{
                "ts": dt.datetime.utcnow(),
                "product": product,
                "ttype": ttype,
                "unit": unit,
                "qty": qty,
                "price": price,
                "total": total,
                "note": note
            }])
            write_df("transactions", df)
            st.success(f"{product} için {ttype} kaydedildi. ({unit}: {qty:g}, fiyat: {price:,.0f} ₺)")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son İşlemler")
    tx = read_sql("transactions")
    st.data_editor(tx, use_container_width=True, disabled=True)

# ---------------------- ÖZBAĞ (HAS) ----------------------
elif page == "Özbağ Fiyatları (Has Referansı)":
    st.subheader("Özbağ Fiyatları (Toptancı / Has Referansı)")
    st.caption("CSV biçimi: Ad,Has  | Örnek: Çeyrek Altın,0.3520  | 24 Ayar Gram için 1.0000")

    txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="ozbag_csv",
                       value="Çeyrek Altın,0.3520\nYarım Altın,0.7040\nTam Altın,1.4080\nAta,1.4160\n24 Ayar Gram,1.0000")
    if st.button("Özbağ İçeri Al"):
        try:
            rows = parse_csv_lines(txt, expect_cols=2)
            data = []
            for name, h in rows:
                data.append({
                    "source": "OZBAG",
                    "name": name.replace("Ata,", "Ata Lira").replace("Ata", "Ata Lira"),
                    "buy": None, "sell": None,
                    "has": to_float(h),
                    "ts": dt.datetime.utcnow()
                })
            upsert_prices(pd.DataFrame(data))
            st.success("Özbağ has çarpanları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son Özbağ Kayıtları")
    odf = read_sql("prices")
    odf = odf[odf["source"] == "OZBAG"][["source","name","has","ts"]]
    st.data_editor(odf, use_container_width=True, disabled=True)

# ---------------------- ENVANTER & KASA ----------------------
elif page == "Envanter & Kasa":
    st.subheader("Envanter (Has Bazlı) ve Kasa (₺)")

    tx = read_sql("transactions")
    if tx.empty:
        st.info("Henüz işlem yok. Lütfen **İşlem (Alış/Satış)** sekmesinden ekleyin.")
    else:
        # Stok adet/gram
        inv = (tx.assign(qty_sign=np.where(tx["ttype"]=="Alış", 1, -1) * tx["qty"])
                 .groupby(["product","unit"], as_index=False)["qty_sign"].sum()
                 .rename(columns={"qty_sign":"stock"}))

        # Has hesabı: Özbağ map
        hmap = ozbag_has_map()

        def to_has(row):
            p = row["product"]
            u = row["unit"]
            qty = row["stock"]
            if p == "24 Ayar Gram" and u == "gram":
                return qty  # 24 ayar 1:1 has
            # Klasik sikkeler için has_map
            if p in hmap:
                # adet ise doğrudan çarp
                return qty * hmap[p]
            return 0.0

        inv["has"] = inv.apply(to_has, axis=1)

        col1, col2 = st.columns([2,1])
        with col1:
            st.markdown("##### Envanter")
            st.data_editor(inv, use_container_width=True, disabled=True)

        with col2:
            # Kasa: satışlar (+), alışlar (–) toplamı
            kasa = float(tx["total"].sum()) if not tx.empty else 0.0
            st.metric("Kasa (₺)", f"{kasa:,.0f}")

            # Toplam has
            total_has = float(inv["has"].sum()) if not inv.empty else 0.0
            st.metric("Toplam Has (gr)", f"{total_has:,.2f}")

# ---------------------- ÖZBAĞ CARİ (HAS) ----------------------
elif page == "Özbağ Cari (Has)":
    st.subheader("Özbağ Cari (Has Takip)")

    col1, col2 = st.columns(2)
    with col1:
        op = st.selectbox("İşlem", ["Borç Ekle (+has)", "Ödeme (-has)"])
    with col2:
        has_amt = st.number_input("Miktar (Has gr)", min_value=0.00, step=0.10, value=0.00)

    note = st.text_input("Açıklama", "")
    if st.button("Kaydet / Cari Güncelle"):
        sign = 1.0 if op == "Borç Ekle (+has)" else -1.0
        df = pd.DataFrame([{
            "ts": dt.datetime.utcnow(),
            "item": op,
            "has": sign * has_amt,
            "note": note
        }])
        write_df("ozbag_cari", df)
        st.success("Cari kayıt güncellendi.")

    st.markdown("#### Cari Ekstresi")
    cdf = read_sql("ozbag_cari")
    st.data_editor(cdf, use_container_width=True, disabled=True)

    total_has = float(cdf["has"].sum()) if not cdf.empty else 0.0
    st.metric("Özbağ’a Net Borç (Has gr)", f"{total_has:,.2f}")