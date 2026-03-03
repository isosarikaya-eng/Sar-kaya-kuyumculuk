import streamlit as st

st.set_page_config(
    page_title="Sarrafiye TV",
    page_icon="💎",
    layout="wide"
)

st.title("💎 Sarrafiye TV")
st.markdown("---")

st.subheader("📊 Günlük HAS Kuru")

if "has_kuru" not in st.session_state:
    st.session_state.has_kuru = 0.0

col1, col2 = st.columns(2)

with col1:
    has_input = st.number_input(
        "HAS Kuru (₺)",
        min_value=0.0,
        step=0.1,
        value=st.session_state.has_kuru
    )

    if st.button("Kaydet"):
        st.session_state.has_kuru = has_input
        st.success("HAS kuru kaydedildi.")

with col2:
    st.metric(
        label="Aktif HAS Kuru",
        value=f"{st.session_state.has_kuru:.2f} ₺"
    )

st.markdown("---")
st.subheader("🔄 TL → HAS Dönüştürme")

tl_amount = st.number_input("TL Tutarı", min_value=0.0, step=100.0)

if st.session_state.has_kuru > 0:
    has_value = tl_amount / st.session_state.has_kuru
    st.success(f"Karşılığı: {has_value:.4f} HAS")
else:
    st.warning("Önce HAS kuru giriniz.")