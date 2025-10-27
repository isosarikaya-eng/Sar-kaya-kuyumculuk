import streamlit as st
import pandas as pd
import datetime as dt

st.set_page_config(page_title="💎 Sarıkaya Kuyumculuk Entegrasyon", layout="wide")

# ==================================
# 🔧 Yardımcı Fonksiyonlar
# ==================================
def parse_harem_csv(csv_text):
    """CSV: Ad,Alış,Satış"""
    try:
        rows = []
        for line in csv_text.splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if len(parts) == 3:
                name, buy, sell = parts
                rows.append({
                    "source": "HAREM",
                    "name": name,
                    "buy": float(buy.replace(",", "").replace(".", "")),
                    "sell": float(sell.replace(",", "").replace(".", "")),
                    "ts": dt.date.today()
                })
        return pd.DataFrame(rows)
    except Exception as e:
        st.error(f"Veri okunamadı: {e}")
        return pd.DataFrame(columns=["source", "name", "buy", "sell", "ts"])


def parse_ozbag_csv(csv_text):
    """CSV: Ad,Has"""
    try:
        rows = []
        for line in csv_text.splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if len(parts) == 2:
                ad, has = parts
                rows.append({"name": ad, "has": float(has)})
        return pd.DataFrame(rows)
    except Exception as e:
        st.error(f"Özbağ verisi okunamadı: {e}")
        return pd.DataFrame(columns=["name", "has"])


def get_latest_price(df, product, ttype):
    """Ürün adına göre Harem fiyatı bulur"""
    eşleştir = {
        "Çeyrek Altın": "Eski Çeyrek",
        "Yarım Altın": "Eski Yarım",
        "Tam Altın": "Eski Tam",
        "Ata Lira": "Eski Ata",
        "24 Ayar Gram": "Gram Altın",
    }
    hedef = eşleştir.get(product, product)
    satır = df[df["name"].str.contains(hedef, case=False, na=False)]
    if satır.empty:
        return None
    fiyat = satır.iloc[0]["sell" if ttype == "Satış" else "buy"]
    return fiyat


# ==================================
# 🧭 Sekme Yapısı
# ==================================
tabs = st.tabs(["📊 Harem Fiyatları", "💱 Alış / Satış", "🏦 Özbağ Fiyatları", "🏪 Kasa & Envanter"])

# ==================================
# 📊 HAREM FİYATLARI
# ==================================
with tabs[0]:
    st.header("📊 Harem Fiyatları (Müşteri Bazlı)")
    st.caption("CSV formatı: Ad,Alış,Satış")
    harem_input = st.text_area("CSV'yi buraya yapıştır", key="harem_input")
    if st.button("Harem Verisini Kaydet", key="btn_harem"):
        df = parse_harem_csv(harem_input)
        st.session_state["harem_df"] = df
        st.success("✅ Harem verisi kaydedildi.")
    if "harem_df" in st.session_state:
        st.dataframe(st.session_state["harem_df"], use_container_width=True)

# ==================================
# 💱 ALIŞ / SATIŞ PANELİ
# ==================================
with tabs[1]:
    st.header("💱 İşlem (Alış / Satış)")
    if "harem_df" not in st.session_state or st.session_state["harem_df"].empty:
        st.warning("⚠️ Önce Harem fiyatlarını girin.")
    else:
        ürün = st.selectbox("Ürün Seç", ["Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira", "24 Ayar Gram"])
        tür = st.radio("İşlem Türü", ["Alış", "Satış"], horizontal=True)
        miktar = st.number_input("Adet / Gram", min_value=0.01, value=1.00)
        fiyat = get_latest_price(st.session_state["harem_df"], ürün, tür)
        if fiyat:
            st.metric(label="Önerilen Fiyat", value=f"{fiyat:,.2f} ₺")
            st.success(f"Toplam: {fiyat * miktar:,.2f} ₺")
        else:
            st.error("Bu ürün için fiyat bulunamadı.")

# ==================================
# 🏦 ÖZBAĞ FİYATLARI
# ==================================
with tabs[2]:
    st.header("🏦 Özbağ Fiyatları (Toptancı Has Referansı)")
    st.caption("CSV formatı: Ad,Has")
    ozbag_input = st.text_area("CSV'yi buraya yapıştır", key="ozbag_input")
    if st.button("Özbağ Verisini Kaydet", key="btn_ozbag"):
        df = parse_ozbag_csv(ozbag_input)
        st.session_state["ozbag_df"] = df
        st.success("✅ Özbağ verisi kaydedildi.")
    if "ozbag_df" in st.session_state:
        st.dataframe(st.session_state["ozbag_df"], use_container_width=True)

# ==================================
# 🏪 KASA & ENVANTER
# ==================================
with tabs[3]:
    st.header("🏪 Kasa ve Envanter")
    varsayılan_ürünler = [
        "Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira",
        "24 Ayar Gram", "22 Ayar Gram", "22 Ayar 0.5g", "22 Ayar 0.25g", "₺ Nakit"
    ]
    if "envanter" not in st.session_state:
        st.session_state["envanter"] = {u: 0.0 for u in varsayılan_ürünler}

    ürün = st.selectbox("Ürün", varsayılan_ürünler, key="envanter_ürün")
    miktar = st.number_input("Miktar", min_value=0.0, value=0.0, key="envanter_miktar")
    işlem = st.radio("İşlem Türü", ["Ekle", "Çıkar"], horizontal=True, key="envanter_işlem")

    if st.button("Güncelle", key="btn_envanter"):
        if işlem == "Ekle":
            st.session_state["envanter"][ürün] += miktar
        else:
            st.session_state["envanter"][ürün] -= miktar
        st.success(f"✅ {ürün} stoğu güncellendi.")

    st.dataframe(pd.DataFrame(st.session_state["envanter"].items(), columns=["Ürün", "Miktar"]), use_container_width=True)