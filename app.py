import streamlit as st
import pandas as pd
from sqlalchemy import create_engine

# --- Sayfa Başlığı ---
st.set_page_config(page_title="Sarıkaya Kuyumculuk Envanter", layout="wide")
st.title("💎 Sarıkaya Kuyumculuk - Envanter ve Has Takip Sistemi")

# --- Veri Tabanı Bağlantısı (Geçici Hafızada) ---
engine = create_engine('sqlite:///sarikkaya_envanter.db')

# --- Menüler ---
menu = st.sidebar.selectbox("Menü", ["Stok Girişi", "Borç/Alacak", "Envanter Raporu"])

# --- Stok Girişi Sayfası ---
if menu == "Stok Girişi":
    st.subheader("📦 Yeni Stok Girişi")
    urun_adi = st.text_input("Ürün Adı")
    agirlik = st.number_input("Ağırlık (gr)", min_value=0.0)
    has_orani = st.number_input("Has Oranı (%)", min_value=0.0, max_value=100.0, value=91.6)
    adet = st.number_input("Adet", min_value=1)
    kategori = st.selectbox("Kategori", ["Bilezik", "Çeyrek", "Yarım", "Tam", "Ata Lira", "Diğer"])
    fiyat_turu = st.selectbox("Fiyat Türü", ["Harem Altın", "Özbağ (Has)"])
    kaydet = st.button("Kaydet")

    if kaydet:
        df = pd.DataFrame({
            "Ürün Adı": [urun_adi],
            "Kategori": [kategori],
            "Ağırlık (gr)": [agirlik],
            "Has Oranı (%)": [has_orani],
            "Adet": [adet],
            "Fiyat Türü": [fiyat_turu]
        })
        df.to_sql("stok", con=engine, if_exists="append", index=False)
        st.success(f"{urun_adi} başarıyla eklendi ✅")

# --- Borç / Alacak ---
elif menu == "Borç/Alacak":
    st.subheader("💰 Borç / Alacak Kaydı")
    kisi = st.text_input("Kişi / Firma Adı")
    tutar = st.number_input("Tutar (₺)", min_value=0.0)
    durum = st.selectbox("Durum", ["Alacak", "Borç"])
    kaydet = st.button("Kaydı Ekle")

    if kaydet:
        df = pd.DataFrame({
            "Kişi": [kisi],
            "Tutar (₺)": [tutar],
            "Durum": [durum]
        })
        df.to_sql("borc_alacak", con=engine, if_exists="append", index=False)
        st.success(f"{kisi} için {durum} kaydı eklendi ✅")

# --- Envanter Raporu ---
elif menu == "Envanter Raporu":
    st.subheader("📊 Mevcut Envanter ve Has Değeri")

    try:
        stok_df = pd.read_sql("SELECT * FROM stok", con=engine)
        borc_df = pd.read_sql("SELECT * FROM borc_alacak", con=engine)

        toplam_has = (stok_df["Ağırlık (gr)"] * stok_df["Has Oranı (%)"] / 100).sum()
        toplam_borc = borc_df.loc[borc_df["Durum"] == "Borç", "Tutar (₺)"].sum()
        toplam_alacak = borc_df.loc[borc_df["Durum"] == "Alacak", "Tutar (₺)"].sum()

        st.metric("Toplam Has (gr)", f"{toplam_has:.2f}")
        st.metric("Toplam Alacak (₺)", f"{toplam_alacak:,.2f}")
        st.metric("Toplam Borç (₺)", f"{toplam_borc:,.2f}")

        st.dataframe(stok_df)
        st.dataframe(borc_df)

    except Exception:
        st.info("Henüz veri eklenmemiş. Önce stok veya borç/alacak kaydı ekleyin.")
