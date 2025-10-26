# -*- coding: utf-8 -*-
import io, datetime as dt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine

# ---------------------------------
# VERİTABANI
# ---------------------------------
# Not: Aynı DB adını koruyoruz ki mevcut kayıtların kalsın
engine = create_engine("sqlite:///sarikkaya_envanter.db", echo=False)

def read_sql(name: str) -> pd.DataFrame:
    try:
        return pd.read_sql(f"SELECT * FROM {name}", con=engine)
    except Exception:
        return pd.DataFrame()

def write_df(name: str, df: pd.DataFrame, if_exists="append"):
    df.to_sql(name, con=engine, if_exists=if_exists, index=False)

# ---------------------------------
# ÜRÜN KARTLARI & FİYAT KURALLARI
# ---------------------------------
PRODUCTS = {
    "Çeyrek Altın":   {"unit": "adet", "std_weight": 1.75,  "purity": 0.916, "sell_add":  50, "buy_sub":  50, "harem_key": "Çeyrek", "ozbag_key": "Çeyrek"},
    "Yarım Altın":    {"unit": "adet", "std_weight": 3.50,  "purity": 0.916, "sell_add": 100, "buy_sub": 100, "harem_key": "Yarım",  "ozbag_key": "Yarım"},
    "Tam Altın":      {"unit": "adet", "std_weight": 7.00,  "purity": 0.916, "sell_add": 200, "buy_sub": 200, "harem_key": "Tam",    "ozbag_key": "Tam"},
    "Ata Lira":       {"unit": "adet", "std_weight": 7.216, "purity": 0.916, "sell_add": 200, "buy_sub": 200, "harem_key": "Ata",    "ozbag_key": "Ata"},
    "24 Ayar Gram":   {"unit": "gram", "std_weight": 1.00,  "purity": 0.995, "sell_add":  10, "buy_sub":  20, "harem_key": "Gram 24 Ayar", "ozbag_key": "Gram 24 Ayar"},
}

# ---------------------------------
# YARDIMCI FONKSİYONLAR
# ---------------------------------
def latest_prices(source: str) -> pd.DataFrame:
    df = read_sql("prices")
    if df.empty:
        return df
    df = df[df["source"] == source].sort_values("ts", ascending=False)
    # aynı isimde birden fazla kayıt varsa en son geleni al
    df = df.drop_duplicates(subset=["name"], keep="first")
    return df[["name", "buy", "sell", "ts"]].reset_index(drop=True)

def get_price(source: str, name: str, field: str = "sell") -> float | None:
    df = latest_prices(source)
    if df.empty:
        return None
    m = df[df["name"] == name]
    if m.empty:
        return None
    return float(m.iloc[0][field])

def suggested_price(product_name: str, ttype: str) -> float | None:
    p = PRODUCTS[product_name]
    base = get_price("HAREM", p["harem_key"], "sell")
    if base is None:
        return None
    if ttype == "Satış":
        return base + p["sell_add"]
    else:  # Alış
        return max(0.0, base - p["buy_sub"])

def compute_has(product_name: str, qty_or_gram: float) -> float:
    p = PRODUCTS[product_name]
    if p["unit"] == "adet":
        total_weight = qty_or_gram * p["std_weight"]
    else:
        total_weight = qty_or_gram
    return total_weight * p["purity"]

# ---------------------------------
# UI
# ---------------------------------
st.set_page_config(page_title="Sarıkaya Kuyumculuk", layout="wide")
st.title("💎 Sarıkaya Kuyumculuk – Envanter & Fiyat Entegrasyonu")

page = st.sidebar.radio("Menü", ["Fiyatlar (Özbağ & Harem)", "İşlem (Alış/Satış)", "Envanter Raporu"])

# ---------------- FİYATLAR ----------------
if page == "Fiyatlar (Özbağ & Harem)":
    st.subheader("Harem Fiyatları (Müşteri Baz Fiyatı)")
    st.caption("CSV biçimi: Ad,Alış,Satış  | Örnek: Çeyrek,0,3600")
    h_txt = st.text_area("CSV'yi buraya yapıştır", height=120, key="harem_csv")
    if st.button("Harem İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(h_txt))
            df["source"] = "HAREM"
            df["ts"] = dt.datetime.utcnow()
            write_df("prices", df[["source","name","buy","sell","ts"]], if_exists="append")
            st.success("Harem fiyatları kaydedildi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.subheader("Özbağ Fiyatları (Toptancı / Has Maliyeti Referansı)")
    st.caption("CSV biçimi: Ad,Alış,Satış  | Örnek: Çeyrek,0,3520")
    o_txt = st.text_area("CSV'yi buraya yapıştır", height=120, key="ozbag_csv")
    if st.button("Özbağ İçeri Al"):
        try:
            df = pd.read_csv(io.StringIO(o_txt))
            df["source"] = "OZBAG"
            df["ts"] = dt.datetime.utcnow()
            write_df("prices", df[["source","name","buy","sell","ts"]], if_exists="append")
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

# ---------------- İŞLEM ----------------
elif page == "İşlem (Alış/Satış)":
    st.subheader("📦 İşlem Girişi")
    product_name = st.selectbox("Ürün", list(PRODUCTS.keys()))
    ttype = st.radio("Tür", ["Satış", "Alış"], horizontal=True)
    unit = PRODUCTS[product_name]["unit"]

    if unit == "adet":
        qty = st.number_input("Adet", min_value=1.0, value=1.0, step=1.0)
        qty_or_gram = qty
        unit_label = "Adet"
    else:
        gram = st.number_input("Gram", min_value=0.01, value=1.00, step=0.01, format="%.2f")
        qty_or_gram = gram
        unit_label = "Gram"

    sug = suggested_price(product_name, ttype)
    price = st.number_input("Birim Fiyat (TL)", value=float(sug or 0.0), min_value=0.0, step=1.0)

    note = st.text_input("Not (opsiyonel)")
    if st.button("Kaydet"):
        has_grams = compute_has(product_name, qty_or_gram)
        total = price * qty_or_gram
        df = pd.DataFrame([{
            "date": dt.date.today().isoformat(),
            "product": product_name,
            "ttype": ttype,
            "unit": unit,
            "qty_or_gram": qty_or_gram,
            "unit_price": price,
            "total": total,
            "has_grams": has_grams if ttype == "Alış" else -has_grams,
            "note": note
        }])
        write_df("transactions", df)
        st.success(f"{product_name} için {ttype} kaydedildi. ({unit_label}: {qty_or_gram}, Fiyat: {price:.0f}₺)")

    st.caption("Önerilen fiyatlar Harem satış fiyatına göre marj uygulanarak hesaplanır; envanter has maliyet referansı Özbağ’dır.")

# ---------------- ENVANTER ----------------
else:
    st.subheader("📊 Envanter (Has Bazlı)")
    tx = read_sql("transactions")
    if tx.empty:
        st.info("Henüz işlem yok. Lütfen 'İşlem' sekmesinden alış/satış ekleyin.")
    else:
        total_has = tx["has_grams"].sum()
        st.metric("Toplam Has (gr)", f"{total_has:,.2f}")
        st.dataframe(tx.sort_values("date", ascending=False).reset_index(drop=True))

        # İsteğe bağlı: Özbağ 24 ayar gram satışını referans alıp TL karşılığı göster
        oz_24 = get_price("OZBAG", "Gram 24 Ayar", "sell")
        if oz_24:
            st.metric("Has Karşılığı (TL) – Özbağ 24 Ayar Satış",
                      f"{(total_has * oz_24):,.0f} ₺")
