# app.py — Sarıkaya Kuyumculuk / Baştan yazım (fiyat çekimi güçlendirilmiş)

import io
import time
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", page_icon="💎", layout="wide")

DB_URL = "sqlite:///data.db"
engine = create_engine(DB_URL, future=True)

# ---------------------- ÜRÜNLER ----------------------
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet"},
    "Yarım Altın": {"unit": "adet"},
    "Tam Altın": {"unit": "adet"},
    "Ata Lira": {"unit": "adet"},
    "24 Ayar Gram": {"unit": "gram"},
    "22 Ayar Gram": {"unit": "gram"},
    "22 Ayar 0,5 gr": {"unit": "adet"},
    "22 Ayar 0,25 gr": {"unit": "adet"},
}

# Harem isim eşlemeleri (öncelik sırası yukarıdan aşağıya)
HAREM_NAME_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın": ["Eski Yarım", "Yarım"],
    "Tam Altın": ["Eski Tam", "Tam"],
    "Ata Lira": ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "24 Ayar Gram", "GRAM ALTIN"],
}

DEFAULT_OZBAG_HAS = {
    "Çeyrek Altın": 0.3520,
    "Yarım Altın": 0.7040,
    "Tam Altın": 1.4080,
    "Ata Lira": 1.4160,
    "24 Ayar Gram": 1.0000,
}

# ---------------------- DB ŞEMALARI ----------------------
def init_db():
    with engine.begin() as conn:
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS prices(
          source TEXT,      -- HAREM / OZBAG
          name   TEXT,
          buy    REAL,
          sell   REAL,
          has    REAL,
          ts     TIMESTAMP
        );""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS transactions(
          ts TIMESTAMP, product TEXT, ttype TEXT, unit TEXT,
          qty REAL, price REAL, total REAL, note TEXT
        );""")
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS ozbag_cari(
          ts TIMESTAMP, item TEXT, has REAL, note TEXT
        );""")

@st.cache_data(ttl=5.0, show_spinner=False)
def read_sql(table: str) -> pd.DataFrame:
    try:
        df = pd.read_sql_table(table, engine)
        # Dtype sağlamlaştırma (string olarak kalan sayıları numeriğe çevir)
        for col in ("buy", "sell", "has", "qty", "price", "total"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
            df = df.sort_values("ts", ascending=False)
        return df
    except Exception:
        return pd.DataFrame()

def write_df(table: str, df: pd.DataFrame):
    if not df.empty:
        df.to_sql(table, engine, if_exists="append", index=False)

def upsert_prices(rows: pd.DataFrame):
    rows = rows.copy()
    rows["ts"] = dt.datetime.utcnow()
    write_df("prices", rows)

# ---------------------- ARAÇLAR ----------------------
def to_float(val) -> float:
    if isinstance(val, (int, float, np.floating)):
        return float(val)
    s = str(val).strip()
    # "5.836,65" -> 5836.65 , "5,924.87" -> 5924.87
    s = s.replace(" ", "")
    # Eğer hem nokta hem virgül varsa ve virgül sağda 2–3 hane ise virgülü ondalık kabul et
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        # Sadece virgül varsa onu ondalık kabul et
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return np.nan

def parse_csv_lines(txt: str, expect_cols=3):
    rows = []
    for raw in txt.strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        while len(parts) < expect_cols:
            parts.append("")
        rows.append(parts[:expect_cols])
    return rows

def latest_price_verbose(source: str, candidate_names: list[str], field: str):
    """
    Aynı isimden birden çok kayıt olsa bile EN YENİ kaydı alır.
    Dönen: (value, matched_name, timestamp) veya (None, None, None)
    """
    df = read_sql("prices")
    if df.empty: 
        return None, None, None
    df = df[df["source"] == source].copy()
    if df.empty:
        return None, None, None
    df["name_norm"] = df["name"].astype(str).str.strip().str.lower()
    for nm in candidate_names:
        nm_norm = nm.strip().lower()
        sub = df[df["name_norm"] == nm_norm].sort_values("ts", ascending=False)
        if not sub.empty and field in sub.columns:
            val = sub.iloc[0][field]
            if pd.notnull(val):
                return float(val), sub.iloc[0]["name"], sub.iloc[0]["ts"]
    return None, None, None

def ozbag_has_map() -> dict:
    df = read_sql("prices")
    cmap = DEFAULT_OZBAG_HAS.copy()
    if df.empty:
        return cmap
    df = df[(df["source"]=="OZBAG") & df["name"].notna() & df["has"].notna()]
    if not df.empty:
        latest = df.sort_values("ts", ascending=False).drop_duplicates(["name"])
        for _, r in latest.iterrows():
            try:
                cmap[str(r["name"])] = float(r["has"])
            except Exception:
                pass
    return cmap

def suggested_price(product: str, ttype: str,
                    gram_buy_offset: float, gram_sell_offset: float,
                    coin_buy_offset: float, coin_sell_offset: float):
    """
    Döner: (öneri_fiyatı or None, debug_dict)
    debug_dict: {'base_sell':..., 'matched':'...', 'ts':...}
    """
    dbg = {"base_sell": None, "matched": None, "ts": None}
    if product in ["Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira", "24 Ayar Gram"]:
        aliases = HAREM_NAME_ALIASES.get(product, [product])
        base_sell, matched, ts = latest_price_verbose("HAREM", aliases, "sell")
        dbg.update({"base_sell": base_sell, "matched": matched, "ts": ts})
        if base_sell is None:
            return None, dbg
        if product == "24 Ayar Gram":
            price = base_sell - gram_buy_offset if ttype == "Alış" else base_sell + gram_sell_offset
        else:
            price = base_sell - coin_buy_offset if ttype == "Alış" else base_sell + coin_sell_offset
        return float(price), dbg
    return None, dbg

def auto_refresh(seconds=10):
    key = "_tick"
    now = time.time()
    if key not in st.session_state:
        st.session_state[key] = now
    elif now - st.session_state[key] >= seconds:
        st.session_state[key] = now
        st.rerun()

# ---------------------- SIDEBAR ----------------------
with st.sidebar:
    st.markdown("### ⚙️ Marj Ayarları")
    a, b = st.columns(2)
    gram_buy_offset  = a.number_input("Gram Alış Offset (₺)", value=20.0, step=1.0)
    gram_sell_offset = b.number_input("Gram Satış Offset (₺)", value=10.0, step=1.0)
    c, d = st.columns(2)
    coin_buy_offset  = c.number_input("Eski Sikkeler Alış Offset (₺)", value=50.0, step=1.0)
    coin_sell_offset = d.number_input("Eski Sikkeler Satış Offset (₺)", value=50.0, step=1.0)
    st.markdown("---")
    page = st.radio("Menü", [
        "Harem Fiyatları (Müşteri Bazı)",
        "İşlem (Alış/Satış)",
        "Özbağ Fiyatları (Has Referansı)",
        "Envanter & Kasa",
        "Özbağ Cari (Has)"
    ])

st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")
init_db()

# ---------------------- HAREM ----------------------
if page == "Harem Fiyatları (Müşteri Bazı)":
    st.subheader("Harem Fiyatları (Müşteri Bazı)")
    st.caption("CSV: Ad,Alış,Satış  | Ör: Eski Çeyrek,9516,9644  /  Gram Altın,5.836,65,5.924,87")

    txt = st.text_area("CSV'yi yapıştırın", height=140, key="harem_csv")
    if st.button("Harem İçeri Al"):
        try:
            rows = parse_csv_lines(txt, expect_cols=3)
            data = []
            for name, b, s in rows:
                data.append({
                    "source":"HAREM", "name":name.strip(),
                    "buy": to_float(b), "sell": to_float(s),
                    "has": None, "ts": dt.datetime.utcnow()
                })
            upsert_prices(pd.DataFrame(data))
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son Harem Kayıtları")
    df = read_sql("prices")
    df = df[df["source"]=="HAREM"][["source","name","buy","sell","ts"]]
    st.data_editor(df, use_container_width=True, disabled=True)

# ---------------------- İŞLEM ----------------------
elif page == "İşlem (Alış/Satış)":
    auto_refresh(10)  # öneri 10 sn’de bir tazelensin
    st.subheader("İşlem (Alış/Satış)")
    st.caption("Öneri, Harem'deki **son satış** satırından hesaplanır (10 sn auto-refresh).")

    product = st.selectbox("Ürün", list(PRODUCTS.keys()))
    ttype = st.radio("Tür", ["Satış","Alış"], index=1, horizontal=True)
    unit = PRODUCTS[product]["unit"]
    qty = st.number_input("Adet" if unit=="adet" else "Gram",
                          min_value=1.0 if unit=="adet" else 0.01,
                          step=1.0 if unit=="adet" else 0.01,
                          value=1.0)

    suggested, dbg = suggested_price(product, ttype,
                                     gram_buy_offset, gram_sell_offset,
                                     coin_buy_offset, coin_sell_offset)

    left, right = st.columns([2,1])
    with left:
        price = st.number_input("Birim Fiyat (TL)", min_value=0.0,
                                value=float(suggested) if suggested else 0.0, step=1.0)
    with right:
        st.metric("Öneri", f"{suggested:,.0f} ₺" if suggested else "—")

    with st.expander("🔎 Fiyat çekim debug"):
        st.write({
            "product": product,
            "ttype": ttype,
            "base_sell": dbg["base_sell"],
            "matched_name": dbg["matched"],
            "ts": str(dbg["ts"])
        })

    if suggested is not None:
        if ttype=="Satış" and price < suggested:
            st.warning("⚠️ Önerinin **altında** satış.")
        if ttype=="Alış" and price > suggested:
            st.warning("⚠️ Önerinin **üstünde** alış.")

    note = st.text_input("Not","")
    if st.button("Kaydet"):
        sign = -1 if ttype=="Alış" else 1
        total = sign * qty * price
        write_df("transactions", pd.DataFrame([{
            "ts": dt.datetime.utcnow(), "product": product, "ttype": ttype,
            "unit": unit, "qty": qty, "price": price, "total": total, "note": note
        }]))
        st.success("İşlem kaydedildi.")

    st.markdown("#### Son İşlemler")
    tx = read_sql("transactions")
    st.data_editor(tx, use_container_width=True, disabled=True)

# ---------------------- ÖZBAĞ ----------------------
elif page == "Özbağ Fiyatları (Has Referansı)":
    st.subheader("Özbağ Fiyatları (Has Referansı)")
    st.caption("CSV: Ad,Has  | Ör: Çeyrek Altın,0.3520  /  24 Ayar Gram,1.0000")

    txt = st.text_area("CSV'yi yapıştırın", height=120, key="ozbag_csv",
                       value="Çeyrek Altın,0.3520\nYarım Altın,0.7040\nTam Altın,1.4080\nAta Lira,1.4160\n24 Ayar Gram,1.0000")
    if st.button("Özbağ İçeri Al"):
        try:
            rows = parse_csv_lines(txt, expect_cols=2)
            data = []
            for name, h in rows:
                data.append({
                    "source":"OZBAG",
                    "name": name.replace("Ata","Ata Lira").strip(),
                    "buy": None, "sell": None, "has": to_float(h),
                    "ts": dt.datetime.utcnow()
                })
            upsert_prices(pd.DataFrame(data))
            st.success("Özbağ has çarpanları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("#### Son Özbağ Kayıtları")
    odf = read_sql("prices")
    odf = odf[odf["source"]=="OZBAG"][["source","name","has","ts"]]
    st.data_editor(odf, use_container_width=True, disabled=True)

# ---------------------- ENVANTER & KASA ----------------------
elif page == "Envanter & Kasa":
    st.subheader("Envanter (Has Bazlı) & Kasa (₺)")
    tx = read_sql("transactions")
    if tx.empty:
        st.info("Henüz işlem yok.")
    else:
        inv = (tx.assign(qty_sign=np.where(tx["ttype"]=="Alış", 1, -1)*tx["qty"])
                 .groupby(["product","unit"], as_index=False)["qty_sign"].sum()
                 .rename(columns={"qty_sign":"stock"}))
        hmap = ozbag_has_map()

        def to_has(row):
            p, u, q = row["product"], row["unit"], row["stock"]
            if p=="24 Ayar Gram" and u=="gram": return q
            if p in hmap: return q*hmap[p]
            return 0.0

        inv["has"] = inv.apply(to_has, axis=1)
        c1, c2 = st.columns([2,1])
        with c1:
            st.data_editor(inv, use_container_width=True, disabled=True)
        with c2:
            kasa = float(tx["total"].sum())
            st.metric("Kasa (₺)", f"{kasa:,.0f}")
            st.metric("Toplam Has (gr)", f"{float(inv['has'].sum()):,.2f}")

# ---------------------- ÖZBAĞ CARİ ----------------------
elif page == "Özbağ Cari (Has)":
    st.subheader("Özbağ Cari (Has)")
    op = st.selectbox("İşlem", ["Borç Ekle (+has)", "Ödeme (-has)"])
    amt = st.number_input("Has (gr)", min_value=0.00, step=0.10, value=0.00)
    note = st.text_input("Açıklama","")
    if st.button("Cari Kaydet"):
        sign = 1.0 if op.startswith("Borç") else -1.0
        write_df("ozbag_cari", pd.DataFrame([{
            "ts": dt.datetime.utcnow(), "item": op, "has": sign*amt, "note": note
        }]))
        st.success("Cari güncellendi.")
    st.markdown("#### Ekstre")
    cdf = read_sql("ozbag_cari")
    st.data_editor(cdf, use_container_width=True, disabled=True)
    st.metric("Özbağ’a Net Borç (Has gr)", f"{float(cdf['has'].sum() if not cdf.empty else 0):,.2f}")