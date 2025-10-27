import streamlit as st
import pandas as pd
import datetime as dt

st.set_page_config(page_title="💎 Sarıkaya Kuyumculuk Entegrasyon", layout="wide")

# ========================================
# 🧠 Yardımcı Fonksiyonlar
# ========================================
def temiz_fiyat(deger):
    """Nokta-virgül karışıklığını düzeltip float döner"""
    try:
        deger = deger.replace(" ", "").replace("₺", "")
        if "," in deger and "." in deger:
            if deger.find(",") > deger.find("."):
                deger = deger.replace(".", "").replace(",", ".")
            else:
                deger = deger.replace(",", "")
        else:
            deger = deger.replace(",", ".")
        return float(deger)
    except:
        return None

def parse_harem_csv(csv_text):
    """CSV biçimi: Ad,Alış,Satış"""
    satırlar = []
    for line in csv_text.splitlines():
        parça = [p.strip() for p in line.split(",")]
        if len(parça) == 3:
            ad, alış, satış = parça
            satırlar.append({
                "source": "HAREM",
                "name": ad,
                "buy": temiz_fiyat(alış),
                "sell": temiz_fiyat(satış),
                "ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            })
    return pd.DataFrame(satırlar)

def parse_ozbag_csv(csv_text):
    """CSV biçimi: Ad,Has"""
    satırlar = []
    for line in csv_text.splitlines():
        parça = [p.strip() for p in line.split(",")]
        if len(parça) == 2:
            ad, has = parça
            satırlar.append({"name": ad, "has": temiz_fiyat(has)})
    return pd.DataFrame(satırlar)

def fiyat_getir(df, ürün, tür):
    """Harem tablosundan ürünün alış/satış fiyatını döner"""
    eşleştir = {
        "Çeyrek Altın": "Eski Çeyrek",
        "Yarım Altın": "Eski Yarım",
        "Tam Altın": "Eski Tam",
        "Ata Lira": "Eski Ata",
        "24 Ayar Gram": "Gram Altın"
    }
    hedef = eşleştir.get(ürün, ürün)
    satır = df[df["name"].str.contains(hedef, case=False, na=False)]
    if satır.empty:
        return None
    return satır.iloc[0]["sell" if tür == "Satış" else "buy"]

# ========================================
# 🧭 Sekmeler
# ========================================
tabs = st.tabs(["📊 Harem Fiyatları", "💱 Alış / Satış", "🏦 Özbağ Fiyatları", "🏪 Kasa & Envanter"])

# 📊 HAREM
with tabs[0]:
    st.header("📊 Harem Fiyatları")
    st.caption("CSV formatı: Ad,Alış,Satış  |  Örnek: Eski Çeyrek,9516,9644")
    harem_input = st.text_area("CSV'yi buraya yapıştır", key="harem_input")
    if st.button("Harem İçeri Al", key="btn_harem"):
        df = parse_harem_csv(harem_input)
        st.session_state["harem_df"] = df
        st.success("✅ Harem fiyatları kaydedildi.")
    if "harem_df" in st.session_state:
        st.dataframe(st.session_state["harem_df"], use_container_width=True)

# 💱 ALIŞ / SATIŞ
with tabs[1]:
    st.header("💱 Alış / Satış İşlemi")
    if "harem_df" not in st.session_state:
        st.warning("⚠️ Önce Harem fiyatlarını girin.")
    else:
        ürün = st.selectbox("Ürün Seç", ["Çeyrek Altın","Yarım Altın","Tam Altın","Ata Lira","24 Ayar Gram"])
        tür = st.radio("İşlem Türü", ["Alış","Satış"], horizontal=True)
        miktar = st.number_input("Adet / Gram", min_value=0.01, value=1.00)
        fiyat = fiyat_getir(st.session_state["harem_df"], ürün, tür)
        if fiyat:
            toplam = fiyat * miktar
            st.metric(label="Önerilen Fiyat", value=f"{fiyat:,.2f} ₺")
            st.success(f"Toplam: {toplam:,.2f} ₺")

            # 🔒 Satış fiyatı alışın altındaysa uyar
            alış_fiyatı = fiyat_getir(st.session_state["harem_df"], ürün, "Alış")
            if tür == "Satış" and alış_fiyatı and fiyat < alış_fiyatı:
                st.error(f"⚠️ Satış fiyatı ({fiyat:,.2f} ₺), alış fiyatı ({alış_fiyatı:,.2f} ₺) altında olamaz!")
        else:
            st.error("⚠️ Fiyat bulunamadı.")

# 🏦 ÖZBAĞ
with tabs[2]:
    st.header("🏦 Özbağ Fiyatları (Toptancı Has Referansı)")
    st.caption("CSV biçimi: Ad,Has | Örnek: Çeyrek,0.3520")
    ozbag_input = st.text_area("CSV'yi buraya yapıştır", key="ozbag_input")
    if st.button("Özbağ İçeri Al", key="btn_ozbag"):
        df = parse_ozbag_csv(ozbag_input)
        st.session_state["ozbag_df"] = df
        st.success("✅ Özbağ fiyatları kaydedildi.")
    if "ozbag_df" in st.session_state:
        st.dataframe(st.session_state["ozbag_df"], use_container_width=True)

# 🏪 KASA & ENVANTER
with tabs[3]:
    st.header("🏪 Kasa ve Envanter Durumu")
    varsayılan = [
        "Çeyrek Altın","Yarım Altın","Tam Altın","Ata Lira",
        "24 Ayar Gram","22 Ayar Gram","22 Ayar 0.5g","22 Ayar 0.25g","₺ Nakit"
    ]
    if "envanter" not in st.session_state:
        st.session_state["envanter"] = {v: 0 for v in varsayılan}

    ürün = st.selectbox("Ürün", varsayılan)
    miktar = st.number_input("Miktar", min_value=0.0, value=0.0)
    işlem = st.radio("İşlem", ["Ekle","Çıkar"], horizontal=True)
    if st.button("Güncelle"):
        if işlem == "Ekle":
            st.session_state["envanter"][ürün] += miktar
        else:
            st.session_state["envanter"][ürün] -= miktar
        st.success(f"{ürün} stoğu güncellendi ✅")

    st.dataframe(
        pd.DataFrame(st.session_state["envanter"].items(), columns=["Ürün","Miktar"]),
        use_container_width=True
    )