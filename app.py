# app.py
import streamlit as st
import pandas as pd
import datetime as dt
from decimal import Decimal, InvalidOperation

st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="centered")

# ----------------------------
# Yardımcılar
# ----------------------------
def parse_tr_number(x: str) -> float:
    """
    '5.924,87' -> 5924.87
    '5924,87'  -> 5924.87
    '5924.87'  -> 5924.87
    '5924'     -> 5924.0
    Boş/None   -> NaN
    """
    if x is None:
        return float("nan")
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return float("nan")
    # Türk formatı için nokta binlik, virgül ondalık kabul et
    if "," in s and s.rfind(",") > s.rfind("."):
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(Decimal(s))
    except InvalidOperation:
        return float("nan")

def df_normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    # Sütun adlarını normalize et
    cols = [str(c).strip().lower() for c in df.columns]
    df.columns = cols
    # Beklenen isimlere eşle
    rename = {
        "ad": "name", "ürün": "name", "urun": "name", "isim": "name",
        "alış": "buy", "alis": "buy",
        "satış": "sell", "satis": "sell",
        "kaynak": "source", "src": "source"
    }
    df = df.rename(columns=rename)
    # Zorunlular
    need = ["name", "buy", "sell"]
    for c in need:
        if c not in df.columns:
            df[c] = pd.NA
    # Sayıları temizle
    df["buy"]  = df["buy"].map(parse_tr_number)
    df["sell"] = df["sell"].map(parse_tr_number)
    # Kaynak adı doldur
    if "source" not in df.columns:
        df["source"] = pd.NA
    return df[["source", "name", "buy", "sell"]]

def now_ts():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat()

# Uygulama durumu (hafif & güvenli)
if "prices" not in st.session_state:
    st.session_state.prices = pd.DataFrame(columns=["source", "name", "buy", "sell", "ts"])

# Harem satırlarında kullanılacak esnek eş-adlar
HAREM_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın" : ["Eski Yarım", "Yarım"],
    "Tam Altın"   : ["Eski Tam", "Tam"],
    "Ata Lira"    : ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "Gram 24 Ayar", "24 Ayar Gram"],
}

PRODUCTS = ["Çeyrek Altın", "Yarım Altın", "Tam Altın", "Ata Lira", "24 Ayar Gram"]

# ----------------------------
# Fiyat arama (Harem)
# ----------------------------
def harem_sell_for(product_name: str) -> tuple[float | None, str | None]:
    """
    Ürün için HAREM 'satış' fiyatını bulur.
    Eş-adlardan ilk bulunan satır alınır. (Son yüklenen tablo esas)
    """
    df = st.session_state.prices
    if df.empty:
        return None, None
    # Son kayıtları öncele (son gelen en üstte olsun)
    df = df.sort_values("ts", ascending=False)
    aliases = HAREM_ALIASES.get(product_name, [product_name])
    for a in aliases:
        m = df[(df["source"] == "HAREM") & (df["name"].str.fullmatch(a, case=False, na=False))]
        if not m.empty:
            val = float(m.iloc[0]["sell"])
            return val, a
    return None, None

# ----------------------------
# Marj ayarları (kullanıcıya açık)
# ----------------------------
if "margins" not in st.session_state:
    st.session_state.margins = {
        "24 Ayar Gram": {"buy_delta": -20.0, "sell_delta": +10.0},
        "Çeyrek Altın": {"buy_delta": -50.0, "sell_delta": +50.0},
        "Yarım Altın" : {"buy_delta": -100.0, "sell_delta": +100.0},
        "Tam Altın"   : {"buy_delta": -200.0, "sell_delta": +200.0},
        "Ata Lira"    : {"buy_delta": -200.0, "sell_delta": +200.0},
    }

def suggested_unit_price(product: str, ttype: str) -> tuple[float | None, dict]:
    """
    ttype: 'Alış' veya 'Satış'
    Harem satışını baz alır, marj uygular.
    """
    base_sell, matched = harem_sell_for(product)
    info = {"product": product, "ttype": ttype, "base_sell": base_sell, "matched_name": matched, "ts": now_ts()}
    if base_sell is None:
        return None, info
    mg = st.session_state.margins.get(product, {"buy_delta": 0.0, "sell_delta": 0.0})
    if ttype == "Alış":
        return round(base_sell + mg["buy_delta"], 2), info
    else:
        return round(base_sell + mg["sell_delta"], 2), info

# ----------------------------
# UI
# ----------------------------
st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")

tabs = st.tabs(["📊 Harem Fiyatları", "💷 Alış / Satış", "🏛️ Özbağ Fiyatları", "⚙️ Marj Ayarları"])

# ---- HAREM
with tabs[0]:
    st.subheader("Harem Fiyatları (CSV ile Yapıştır-Yükle)")
    st.caption("CSV biçimi: **Ad,Alış,Satış**  — Örnek:  `Eski Çeyrek,9516,9644`  |  `Gram Altın,5.836,65,5.924,87`")
    txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="harem_input")
    colh1, colh2 = st.columns([1,1])
    with colh1:
        if st.button("Harem İçeri Al", type="primary"):
            try:
                # Satır bazında manuel parse (virgül/nokta uyumu için)
                rows = []
                for line in (txt or "").splitlines():
                    if not line.strip():
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 3:
                        raise ValueError(f"Satır hatalı: {line}")
                    name = parts[0]
                    buy  = parse_tr_number(parts[1])
                    sell = parse_tr_number(parts[2])
                    rows.append(["HAREM", name, buy, sell, now_ts()])
                if not rows:
                    st.warning("Yüklenecek satır bulunamadı.")
                else:
                    newdf = pd.DataFrame(rows, columns=["source", "name", "buy", "sell", "ts"])
                    st.session_state.prices = pd.concat([newdf, st.session_state.prices], ignore_index=True)
                    st.success(f"{len(rows)} satır eklendi.")
            except Exception as e:
                st.error(f"Hata: {e}")
    with colh2:
        if st.button("Tabloyu Temizle", help="Sadece HAREM kaynaklı satırları siler."):
            df = st.session_state.prices
            st.session_state.prices = df[df["source"] != "HAREM"].reset_index(drop=True)
            st.info("HAREM satırları temizlendi.")
    st.write("### Son Harem Kayıtları")
    if st.session_state.prices.empty or st.session_state.prices[st.session_state.prices["source"]=="HAREM"].empty:
        st.info("Henüz Harem kaydı yok.")
    else:
        st.dataframe(st.session_state.prices[st.session_state.prices["source"]=="HAREM"], use_container_width=True)

# ---- ALIŞ / SATIŞ
with tabs[1]:
    st.subheader("Alış / Satış İşlemi")
    st.caption("Öneri, Harem’deki **son satış** satırından hesaplanır (altta ‘Fiyatı güncelle’ ile yenileyebilirsiniz).")

    product = st.selectbox("Ürün Seç", PRODUCTS, index=PRODUCTS.index("24 Ayar Gram"))
    ttype = st.radio("İşlem Türü", ["Alış", "Satış"], horizontal=True, index=1)
    qty = st.number_input("Adet / Gram", min_value=0.01, value=1.00, step=1.0, format="%.2f")

    # Öneriyi getir
    unit_suggest, dbg = suggested_unit_price(product, ttype)

    # Manuel fiyat
    colm1, colm2 = st.columns([1,1])
    with colm1:
        use_manual = st.checkbox("Fiyatı elle gir", value=False)
    with colm2:
        st.button("Fiyatı güncelle")

    if use_manual:
        unit_price = st.number_input(
            "Birim Fiyat (TL)",
            value=float(unit_suggest or 0.0),
            step=1.0,
            format="%.2f",
            help="Elle yazarsanız önerinin yerine kullanılır."
        )
    else:
        unit_price = float(unit_suggest or 0.0)
        st.number_input("Birim Fiyat (TL)", value=unit_price, step=1.0, format="%.2f", disabled=True)

    total = round(unit_price * qty, 2)

    st.write("### Önerilen Fiyat")
    st.markdown(f"<h2 style='margin:0'>{total:,.2f} ₺</h2>", unsafe_allow_html=True)
    st.success(f"Toplam: {total:,.2f} ₺")

    # Güvenlik uyarısı: satış fiyatı Harem satışının < altına düşmesin
    base_sell, matched = harem_sell_for(product)
    if ttype == "Satış" and base_sell is not None and unit_price < base_sell:
        st.error("⚠️ Satış fiyatı **Harem satışının** altında olamaz!")

    with st.expander("🔎 Fiyat çekim debug"):
        st.json(dbg)

# ---- ÖZBAĞ
with tabs[2]:
    st.subheader("Özbağ Fiyatları (Has Referansı)")
    st.caption("CSV biçimi: **Ad,Has**  — Örnek:  `Çeyrek,0.3520`  |  `24 Ayar Gram,1.0000`")
    oz_txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="ozbag_input")
    if st.button("Özbağ İçeri Al"):
        try:
            rows = []
            for line in (oz_txt or "").splitlines():
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    raise ValueError(f"Satır hatalı: {line}")
                name = parts[0]
                has = parse_tr_number(parts[1])
                # Has'ı 'sell' kolonuna koyup source='OZBAG' olarak saklıyoruz (min gereksinim)
                rows.append(["OZBAG", name, float("nan"), has, now_ts()])
            if rows:
                newdf = pd.DataFrame(rows, columns=["source", "name", "buy", "sell", "ts"])
                st.session_state.prices = pd.concat([newdf, st.session_state.prices], ignore_index=True)
                st.success(f"{len(rows)} satır eklendi.")
            else:
                st.warning("Yüklenecek satır bulunamadı.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.write("### Son Özbağ Kayıtları")
    oz = st.session_state.prices[st.session_state.prices["source"]=="OZBAG"]
    if oz.empty:
        st.info("Henüz Özbağ kaydı yok.")
    else:
        st.dataframe(oz, use_container_width=True)

# ---- MARJ AYARLARI
with tabs[3]:
    st.subheader("Marj Ayarları")
    st.caption("Öneri hesapları Harem **satış** fiyatına bu marjlar eklenerek yapılır.")
    for p in PRODUCTS:
        mg = st.session_state.margins.setdefault(p, {"buy_delta": 0.0, "sell_delta": 0.0})
        with st.expander(p, expanded=(p=="24 Ayar Gram")):
            c1, c2 = st.columns(2)
            with c1:
                mg["buy_delta"] = st.number_input(f"{p} • Alış marjı (TL)", value=float(mg["buy_delta"]), step=10.0, format="%.2f", key=f"{p}_buy_delta")
            with c2:
                mg["sell_delta"] = st.number_input(f"{p} • Satış marjı (TL)", value=float(mg["sell_delta"]), step=10.0, format="%.2f", key=f"{p}_sell_delta")
    st.info("Marjlar otomatik kaydedilir ve bellek süresince geçerlidir.")