import io, sqlite3, re, time
from datetime import datetime, timezone
import pandas as pd
import streamlit as st

DB = "data.db"

# ---------- yardımcılar ----------
def get_conn():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS prices (
        source TEXT, name TEXT, buy REAL, sell REAL, ts TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
        dt TEXT, product TEXT, ttype TEXT, unit TEXT,
        qty REAL, unit_price REAL, total REAL, note TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS supplier_moves (
        dt TEXT, vendor TEXT, product TEXT, karat TEXT,
        qty REAL, grams REAL, has_grams REAL, note TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS cash_moves (
        dt TEXT, kind TEXT, amount REAL, note TEXT
    )""")
    conn.commit()
    return conn

def df_read(q, params=()):
    conn = get_conn()
    return pd.read_sql_query(q, conn, params=params)

def df_write(table, df: pd.DataFrame):
    conn = get_conn()
    df.to_sql(table, conn, if_exists="append", index=False)
    conn.commit()

def to_utc_iso(dt=None):
    if dt is None: dt = datetime.now(timezone.utc)
    return dt.isoformat()

# Türkçe/karma sayı -> float
def parse_num(x):
    if x is None: return None
    s = str(x).strip()
    if s == "": return None
    s = s.replace(" ", "")
    # 5.924,87  -> 5924.87
    if re.search(r",\d{1,3}$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except:
        return None

# Harem isim eşleşmeleri
HAREM_ALIASES = {
    "Çeyrek Altın": ["Eski Çeyrek", "Çeyrek"],
    "Yarım Altın": ["Eski Yarım", "Yarım"],
    "Tam Altın": ["Eski Tam", "Tam"],
    "Ata Lira": ["Eski Ata", "Ata"],
    "24 Ayar Gram": ["Gram Altın", "Has Altın", "24 Ayar Gram", "24 Ayar"],
    "22 Ayar Gram": ["22 Ayar", "Gram 22 Ayar"],
    "22 Ayar 0.5g": ["22 Ayar 0,5", "0,5 gr 22 Ayar", "0.5 gr 22 Ayar"],
    "22 Ayar 0.25g": ["22 Ayar 0,25", "0,25 gr 22 Ayar", "0.25 gr 22 Ayar"],
}

# ürün sabitleri
PRODUCTS = {
    "Çeyrek Altın": {"unit": "adet", "std_weight": 1.75, "purity": 0.916},
    "Yarım Altın": {"unit": "adet", "std_weight": 3.50, "purity": 0.916},
    "Tam Altın": {"unit": "adet", "std_weight": 7.00, "purity": 0.916},
    "Ata Lira": {"unit": "adet", "std_weight": 7.216, "purity": 0.916},
    "24 Ayar Gram": {"unit": "gram", "std_weight": 1.00, "purity": 0.995},
    "22 Ayar Gram": {"unit": "gram", "std_weight": 1.00, "purity": 0.916},
    "22 Ayar 0.5g": {"unit": "adet", "std_weight": 0.50, "purity": 0.916},
    "22 Ayar 0.25g": {"unit": "adet", "std_weight": 0.25, "purity": 0.916},
}

# varsayılan marjlar (TL)
DEFAULT_MARGINS = {
    "Çeyrek Altın": (-50, +50),
    "Yarım Altın":  (-100, +100),
    "Tam Altın":    (-200, +200),
    "Ata Lira":     (-200, +200),
    "24 Ayar Gram": (-20, +10),
    "22 Ayar Gram": (-20, +10),
    "22 Ayar 0.5g": (-10, +10),
    "22 Ayar 0.25g":(-5,  +5),
}

def latest_harem_sell(product_name: str):
    """HAREM kaydından en güncel SATIŞ fiyatını ve eşleşen ismi döner."""
    aliases = [a.lower() for a in HAREM_ALIASES.get(product_name, [product_name])]
    df = df_read("SELECT * FROM prices WHERE source='HAREM'")
    if df.empty: return None, None, None
    # ts sıralaması sağlam olsun
    df["ts_parsed"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)
    m = df[df["name"].str.lower().isin(aliases)].sort_values("ts_parsed", ascending=False)
    if m.empty: return None, None, None
    row = m.iloc[0]
    return float(row["sell"]), str(row["name"]), str(row["ts"])

def suggested_price(product, ttype, margins):
    base, match, ts = latest_harem_sell(product)
    if base is None:
        return None, {"reason": "no_harem_record"}
    buy_adj, sell_adj = margins.get(product, (0, 0))
    if ttype == "Satış":
        price = base + sell_adj
    else: # Alış
        price = base + buy_adj
    return price, {"product": product, "ttype": ttype, "base_sell": base,
                   "matched_name": match, "ts": ts}

def record_transaction(product, ttype, qty, unit_price, note=""):
    unit = PRODUCTS[product]["unit"]
    total = round(qty * unit_price, 2)
    row = pd.DataFrame([{
        "dt": to_utc_iso(), "product": product, "ttype": ttype,
        "unit": unit, "qty": qty, "unit_price": unit_price,
        "total": total, "note": note
    }])
    df_write("transactions", row)
    # kasa hareketi
    kind = "in" if ttype == "Satış" else "out"
    cash = pd.DataFrame([{"dt": to_utc_iso(), "kind": kind, "amount": total, "note": f"{ttype} {product}"}])
    df_write("cash_moves", cash)
    return total

def compute_inventory():
    tx = df_read("SELECT * FROM transactions")
    if tx.empty:
        return pd.DataFrame(columns=["product", "unit", "qty", "has_grams"])
    # qty -> has_grams
    out = []
    for p, g in tx.groupby("product"):
        info = PRODUCTS.get(p, {"unit":"adet","std_weight":1.0,"purity":1.0})
        unit = info["unit"]; std = info["std_weight"]; pu = info["purity"]
        qty = g.apply(lambda r: r["qty"] if r["ttype"]=="Alış" else -r["qty"], axis=1).sum()
        has_grams = qty * std * pu if unit=="adet" else qty * pu
        out.append({"product": p, "unit": unit, "qty": round(qty,3), "has_grams": round(has_grams,3)})
    return pd.DataFrame(out).sort_values("product")

def cash_balance():
    c = df_read("SELECT * FROM cash_moves")
    if c.empty: return 0.0
    return round(c.apply(lambda r: r["amount"] if r["kind"]=="in" else -r["amount"], axis=1).sum(), 2)

def margin_state():
    if "margins" not in st.session_state:
        st.session_state.margins = DEFAULT_MARGINS.copy()
    return st.session_state.margins

# ---------- UI ----------
st.set_page_config(page_title="Sarıkaya Kuyumculuk – Entegrasyon", layout="centered")

st.title("💎 Sarıkaya Kuyumculuk – Entegrasyon")

tab_prices, tab_trade, tab_supplier, tab_inventory = st.tabs([
    "📊 Harem Fiyatları", "💱 Alış / Satış", "🏦 Tedarikçi (Özbağ)", "📦 Kasa & Stok"
])

# --- HAREM FİYATLARI ---
with tab_prices:
    st.subheader("Harem Fiyatları (CSV içeri al)")
    st.caption("CSV biçimi: **Ad,Alış,Satış**  | Örnek: `Eski Çeyrek,9516,9644`")
    sample = "Eski Çeyrek,9516,9644\nEski Yarım,19100,19300\nEski Tam,38200,38600\nEski Ata,38400,38800\nGram Altın,5728,5807"
    txt = st.text_area("CSV'yi buraya yapıştırın", height=140, key="harem_csv", value="")
    if st.button("Harem İçeri Al", key="import_harem"):
        try:
            if not txt.strip(): txt = sample
            df = pd.read_csv(io.StringIO(txt), header=None, names=["name","buy","sell"])
            df["buy"]  = df["buy"].map(parse_num)
            df["sell"] = df["sell"].map(parse_num)
            df["source"] = "HAREM"
            df["ts"] = to_utc_iso()
            df = df[["source","name","buy","sell","ts"]]
            df_write("prices", df)
            st.success(f"{len(df)} satır eklendi.")
        except Exception as e:
            st.error(f"Hata: {e}")

    st.markdown("**Son Harem Kayıtları**")
    st.dataframe(df_read("SELECT * FROM prices WHERE source='HAREM' ORDER BY ts DESC LIMIT 20"))

    st.markdown("### ⚙️ Marj Ayarları (TL)")
    margins = margin_state()
    cols = st.columns(2)
    for i, (p,(b,s)) in enumerate(margins.items()):
        with cols[i%2]:
            b2 = st.number_input(f"{p} • Alış marjı (−)", value=float(b), step=1.0, key=f"m_b_{i}")
            s2 = st.number_input(f"{p} • Satış marjı (+)", value=float(s), step=1.0, key=f"m_s_{i}")
            margins[p] = (b2, s2)
    st.info("Öneri fiyat, **Harem SATIŞ** +/− marj ile hesaplanır.")

# --- ALIŞ / SATIŞ ---
with tab_trade:
    st.subheader("Alış / Satış İşlemi")
    st.caption("Öneri, Harem'deki **son satış** satırından hesaplanır (10 sn auto-refresh).")
    st.autorefresh(interval=10_000, key="refresh_trade")

    product = st.selectbox("Ürün Seç", list(PRODUCTS.keys()), key="trade_prod")
    ttype = st.radio("İşlem Türü", ["Alış","Satış"], horizontal=True, key="trade_type")
    qty = st.number_input("Adet / Gram", min_value=0.01, value=1.0, step=1.0, key="trade_qty")

    # öneri
    price, dbg = suggested_price(product, ttype, margin_state())
    st.markdown("##### Önerilen Fiyat")
    if price is None:
        st.error("Harem kaydı bulunamadı. Önce 'Harem Fiyatları' sekmesinden içeri alın.")
    else:
        st.markdown(f"<h2 style='margin-top:-10px'>{price:,.2f} ₺</h2>", unsafe_allow_html=True)

    with st.expander("🔎 Fiyat çekim debug"):
        st.json(dbg if price is not None else {"error":"no harem record"})

    use_manual = st.checkbox("Fiyatı elle gir", key="manual_flag")
    if use_manual:
        manual = st.number_input("Manuel Birim Fiyat (TL)", min_value=0.0, value=price or 0.0, step=1.0, key="manual_price")
        chosen_price = manual
    else:
        chosen_price = price or 0.0

    total = qty * chosen_price
    st.success(f"Toplam: {total:,.2f} ₺")

    # güvenlik uyarısı (Satış fiyatı < Harem satış)
    if ttype=="Satış":
        base, _, _ = latest_harem_sell(product)
        if base is not None and chosen_price < base:
            st.error("⚠️ Satış fiyatı **Harem satış**ın altında olamaz!")

    note = st.text_input("Not (opsiyonel)", key="trade_note")
    if st.button("Kaydet", type="primary", key="trade_save"):
        if chosen_price <= 0:
            st.error("Geçerli bir fiyat girin.")
        else:
            t = record_transaction(product, ttype, qty, chosen_price, note)
            st.success(f"İşlem kaydedildi. Kasa {'+' if ttype=='Satış' else '-'} {t:,.2f} ₺")

# --- TEDARİKÇİ (ÖZBAĞ) ---
with tab_supplier:
    st.subheader("Tedarikçi (Özbağ) – Ürün Girişi ve Borç (Has) Takibi")
    st.caption("Burada fiyat çekimi yok. Ürün ve **has** bilgilerini sen giriyorsun.")
    vendor = st.text_input("Tedarikçi", value="Özbağ", key="sup_vendor")
    sp = st.selectbox("Ürün", list(PRODUCTS.keys()), key="sup_prod")
    karat = st.text_input("Karat/Tip (örn. 22A bilezik)", value="", key="sup_karat")
    unit = PRODUCTS[sp]["unit"]
    qty = st.number_input(f"Miktar ({unit})", min_value=0.01, value=1.0, step=1.0, key="sup_qty")
    grams = qty if unit=="gram" else qty * PRODUCTS[sp]["std_weight"]
    purity = PRODUCTS[sp]["purity"]
    has_grams = grams * purity
    st.write(f"Has (gr): **{has_grams:.3f}**")

    s_note = st.text_input("Not", key="sup_note")
    if st.button("Girişi Kaydet", key="sup_save"):
        row = pd.DataFrame([{
            "dt": to_utc_iso(), "vendor": vendor, "product": sp, "karat": karat,
            "qty": qty, "grams": grams, "has_grams": has_grams, "note": s_note
        }])
        df_write("supplier_moves", row)
        st.success("Tedarikçi girişi kaydedildi.")

    st.markdown("**Son Tedarikçi Kayıtları**")
    st.dataframe(df_read("SELECT * FROM supplier_moves ORDER BY dt DESC LIMIT 50"))

    sup = df_read("SELECT vendor, ROUND(SUM(has_grams),3) AS borc_has FROM supplier_moves GROUP BY vendor")
    st.markdown("### Tedarikçi Has Borç Özeti")
    st.dataframe(sup)

# --- KASA & STOK ---
with tab_inventory:
    st.subheader("Kasa & Stok")
    inv = compute_inventory()
    st.markdown("### Envanter (işlemlerden)")
    st.dataframe(inv)

    st.markdown("### Kasa Durumu (TL)")
    st.metric("Kasa Bakiyesi", f"{cash_balance():,.2f} ₺")

    st.markdown("### Son İşlemler")
    st.dataframe(df_read("SELECT * FROM transactions ORDER BY dt DESC LIMIT 50"))