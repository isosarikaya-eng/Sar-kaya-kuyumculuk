import streamlit as st
import pandas as pd
import datetime as dt
from io import StringIO

st.set_page_config(page_title="💎 Sarıkaya Kuyumculuk Entegrasyon", layout="wide")

# -----------------------------
# 🔧 Yardımcı Fonksiyonlar
# -----------------------------
def parse_csv(text, expected_cols=3):
    data = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        line = line.replace("₺", "").replace(" ", "")
        parts = line.split(",")
        if len(parts) == expected_cols:
            name, buy, sell = parts
            data.append({
                "source": "HAREM",
                "name": name,
                "buy": float(buy.replace(".", "").replace(",", ".")),
                "sell": float(sell.replace(".", "").replace(",", ".")),
                "ts": dt.date.today()
            })
    return pd.DataFrame(data)

def parse_ozbag_csv(text):
    data = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        parts = line.split(",")
        if len(parts) == 2:
            name, has = parts
            data.append({"name": name, "has": float(has)})
    return pd.DataFrame(data)

def get_price(harem_df, product_name, ttype):
    """Harem fiyatlarından doğru ürünü bulur ve marj uygular"""
    mapping = {
        "Çeyrek Altın": "Eski Çeyrek",
        "Yarım Altın": "Eski Yarım",
        "Tam Altın": "Eski Tam",
        "Ata Lira": "Eski Ata",
        "24 Ayar Gram": "Gram Altın",
    }
    match = mapping.get(product_name, product_name)
    row = harem_df[harem_df["name"].str.contains(match, case=False, na=False)]
    if row.empty:
        return None
    price = float(row.iloc[0]["sell" if ttype == "Satış" else "buy"])
    return price

# -----------------------------
# 📊 Sekme Menüsü
# -----------------------------
tabs = st.tabs([
    "Harem Fiyatları (Müşteri Bazı)",
    "İşlem (Alış/Satış)",
    "Özbağ Fiyatları (Toptancı)",
    "Kasa & Envanter"
])

# -----------------------------
# 🟡 HAREM FİYATLARI
# -----------------------------
with tabs[0]:
    st.header("💰 Harem Fiyatları (Müşteri Bazı)")
    st.info("CSV biçimi: Ad,Alış,Satış")
    harem_input = st.text_area("CSV'yi buraya yapıştırın", height=150)
    if st.button("Harem İçeri Al"):
        try:
            df_harem = parse_csv(harem_input)
            st.session_state["harem"] = df_harem
            st.success("✅ Harem fiyatları başarıyla alındı.")
        except Exception as e:
            st.error(f"Hata: {e}")

    if "harem" in st.session_state:
        st.subheader("📄 Son Harem Kayıtları")
        st.dataframe(st.session_state["harem"], use_container_width=True)

# -----------------------------
# 🔁 İŞLEM (ALIŞ / SATIŞ)
# -----------------------------
with tabs[1]:
    st.header("💱 İşlem (Alış / Satış)")
    st.caption("Öneri fiyat Harem'deki son satış/alış değerinden alınır.")

    if "harem" not in st.session_state:
        st.warning("Önce Harem fiyatlarını yükleyin.")
    else:
        df = st.session_state["harem"]
        ürün = st.selectbox("Ürün", ["Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira", "24 Ayar Gram"])
        tür = st.radio("Tür", ["Alış", "Satış"], horizontal=True)
        miktar = st.number_input("Gram / Adet", min_value=0.01, value=1.0)
        birim = get_price(df, ürün, tür)
        if birim:
            st.metric("💸 Önerilen Fiyat", f"{birim:,.2f} ₺", delta=None)
            toplam = miktar * birim
            st.success(f"Toplam Tutar: {toplam:,.2f} ₺")
        else:
            st.error("Fiyat bulunamadı, CSV’yi kontrol edin.")

# -----------------------------
# 🧮 ÖZBAĞ FİYATLARI
# -----------------------------
with tabs[2]:
    st.header("🏦 Özbağ Fiyatları (Has Referans)")
    st.info("CSV biçimi: Ad,Has | Örnek: Çeyrek,0.3520")
    ozbag_input = st.text_area("CSV'yi buraya yapıştırın", height=150)
    if st.button("Özbağ İçeri Al"):
        try:
            df_ozbag = parse_ozbag_csv(ozbag_input)
            st.session_state["ozbag"] = df_ozbag
            st.success("✅ Özbağ verisi başarıyla yüklendi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    if "ozbag" in st.session_state:
        st.subheader("📦 Özbağ Güncel Has Karşılıkları")
        st.dataframe(st.session_state["ozbag"], use_container_width=True)

# -----------------------------
# 🏪 KASA & ENVANTER
# -----------------------------
with tabs[3]:
    st.header("🏪 Kasa ve Envanter Durumu")

    st.markdown("### 💰 Mevcut Stoklar")
    ürünler = [
        "Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira",
        "24 Ayar Gram", "22 Ayar Gram", "22 Ayar 0.5g", "22 Ayar 0.25g", "₺ Nakit"
    ]
    if "envanter" not in st.session_state:
        st.session_state["envanter"] = {u: 0.0 for u in ürünler}

    col1, col2 = st.columns(2)
    with col1:
        seçilen = st.selectbox("Ürün", ürünler)
        miktar = st.number_input("Miktar", min_value=0.0, value=0.0)
    with col2:
        işlem = st.radio("İşlem Türü", ["Ekle", "Çıkar"], horizontal=True)
        if st.button("Kaydet", key="envanter_btn"):
            if işlem == "Ekle":
                st.session_state["envanter"][seçilen] += miktar
            else:
                st.session_state["envanter"][seçilen] -= miktar
            st.success("✅ Güncellendi")

    st.dataframe(pd.DataFrame(st.session_state["envanter"].items(), columns=["Ürün", "Miktar"]), use_container_width=True)