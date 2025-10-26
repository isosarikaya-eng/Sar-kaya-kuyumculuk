# app.py
import io
import datetime as dt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Sarıkaya Kuyumculuk", layout="wide")
st.title("💎 Sarıkaya Kuyumculuk — Fiyat & Envanter Deneme")

# --- Yardımcılar -------------------------------------------------------------
def parse_csv(text, expected=3):
    """
    CSV'yi 'Ad,Alış,Satış' (3 sütun) ya da sadece 'Ad,Has' (2 sütun) formatında okur.
    Nokta/virgül ayracı hatalarını tolere eder.
    """
    text = (text or "").strip()
    if not text:
        return pd.DataFrame()
    # virgül -> nokta düzeltmesi
    fixed = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        # sayılar 1.234,56 şeklinde gelmişse düzelt
        for i in range(1, len(parts)):
            p = parts[i].replace(".", "").replace(",", ".")
            parts[i] = p
        fixed.append(",".join(parts))
    df = pd.read_csv(io.StringIO("\n".join(fixed)), header=None)
    if expected == 3 and df.shape[1] == 3:
        df.columns = ["name", "buy", "sell"]
        for c in ["buy", "sell"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    elif expected == 2 and df.shape[1] == 2:
        df.columns = ["name", "has"]
        df["has"] = pd.to_numeric(df["has"], errors="coerce").fillna(0.0)
    else:
        st.error("CSV biçimi beklenenden farklı.")
        return pd.DataFrame()
    df["ts"] = dt.datetime.utcnow()
    return df

# Harem tarafında “Eski ...” isimlerini baz almak için eş adlar
HAREM_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın": ["Eski Yarım", "Yarım"],
    "Tam Altın":   ["Eski Tam", "Tam"],
    "Ata Lira":    ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "24 Ayar Gram", "Has Altın"],
}

def find_price(df_harem, product, tip="sell"):
    """
    Harem tablosunda ürünün (eş adlarıyla) 'sell' (satış) ya da 'buy' (alış) değerini bul.
    """
    if df_harem is None or df_harem.empty:
        return None
    names = HAREM_ALIASES.get(product, [product])
    for n in names:
        m = df_harem[df_harem["name"].str.strip().str.lower() == n.strip().lower()]
        if not m.empty:
            return float(m.iloc[0][tip])
    return None

def suggested_prices(df_harem):
    """
    Senin marjlarına göre öneri üret:
    - Çeyrek: satış = Harem satış + 50 ; alış = Harem alış - 50
    - Yarım : ±100
    - Tam   : ±200
    - Ata   : ±200
    - 24K gram: satış = Harem satış + 10 ; alış = Harem alış - 20
    """
    rows = []
    rules = {
        "Çeyrek Altın": (50, -50),
        "Yarım Altın":  (100, -100),
        "Tam Altın":    (200, -200),
        "Ata Lira":     (200, -200),
        "24 Ayar Gram": (10, -20),
    }
    for name, (sell_add, buy_add) in rules.items():
        base_sell = find_price(df_harem, name, "sell")
        base_buy  = find_price(df_harem, name, "buy")
        rows.append({
            "ürün": name,
            "harem_satis": base_sell,
            "harem_alis":  base_buy,
            "önerilen_satis": (base_sell + sell_add) if base_sell is not None else None,
            "önerilen_alis":  (base_buy  + buy_add)  if base_buy  is not None else None,
        })
    out = pd.DataFrame(rows)
    return out

# --- UI: Harem CSV -----------------------------------------------------------
st.subheader("Harem Fiyatları (Müşteri Bazı)")
st.caption("CSV biçimi: Ad,Alış,Satış  | Örnek:\n"
           "Eski Çeyrek,9516,9644\nEski Yarım,19100,19300\nGram Altın,5846.4,5934.8")
h_txt = st.text_area("CSV'yi buraya yapıştırın", height=120)
if "df_harem" not in st.session_state:
    st.session_state["df_harem"] = pd.DataFrame(columns=["source","name","buy","sell","ts"])

if st.button("Harem İçeri Al"):
    df = parse_csv(h_txt, expected=3)
    if not df.empty:
        df.insert(0, "source", "HAREM")
        st.session_state["df_harem"] = df
        st.success("Harem fiyatları kaydedildi.")

st.dataframe(st.session_state["df_harem"], use_container_width=True, hide_index=True)

# --- UI: Özbağ CSV (opsiyonel) ----------------------------------------------
st.subheader("Özbağ Fiyatları (Toptancı / Has Referansı)")
st.caption("CSV biçimi: Ad,Has  | Örnek:\nÇeyrek,0.3520\nYarım,0.7040\nTam,1.4080\nAta,1.4160\n24 Ayar Gram,0.2400  (Has TL/gr)")
o_txt = st.text_area("CSV'yi buraya yapıştırın", height=120, key="ozbag_txt")
if "df_ozbag" not in st.session_state:
    st.session_state["df_ozbag"] = pd.DataFrame(columns=["source","name","has","ts"])

if st.button("Özbağ İçeri Al"):
    df = parse_csv(o_txt, expected=2)
    if not df.empty:
        df.insert(0, "source", "OZBAG")
        st.session_state["df_ozbag"] = df
        st.success("Özbağ fiyatları kaydedildi.")

st.dataframe(st.session_state["df_ozbag"], use_container_width=True, hide_index=True)

# --- Önerilen Fiyatlar -------------------------------------------------------
st.subheader("Önerilen Fiyatlar (Marj kurallarıyla)")
if st.session_state["df_harem"].empty:
    st.info("Öneri üretmek için önce Harem CSV girin.")
else:
    sug = suggested_prices(st.session_state["df_harem"])
    st.dataframe(sug, use_container_width=True, hide_index=True)

st.caption("Not: Öneri hesabında Harem’de **Eski Çeyrek/Yarım/Tam/Ata** ve **Gram Altın** satırları baz alınır.")
