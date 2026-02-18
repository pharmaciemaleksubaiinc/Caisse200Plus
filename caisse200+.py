# caisse200+.py
# Registre — Caisse & Boîte (Échange)
# - 3 tabs: CAISSE / BOÎTE / SAUVEGARDE
# - Bold tables + auto height to avoid scrolling even when screen has space
# - Report-style tables: computed cols on same line
# - Inline +/- controls aligned per denomination (below table)
# - Boîte: choose allowed denominations (checkboxes)
# - Autosave daily: JSON state + printable HTML receipt (separate folders)
# - Fix: "type then Enter resets to 0" by LIVE normalisation (no aggressive reindex mid-edit)

import os
import json
import hashlib
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


# ================== CONFIG ==================
st.set_page_config(page_title="Registre — Caisse & Boîte", layout="wide")
TZ = ZoneInfo("America/Toronto")

BASE_DIR = "data"
DIR_CAISSE = os.path.join(BASE_DIR, "records_caisse")
DIR_BOITE = os.path.join(BASE_DIR, "records_boite")
os.makedirs(DIR_CAISSE, exist_ok=True)
os.makedirs(DIR_BOITE, exist_ok=True)

# ================== STYLE (BOLD + SPACE) ==================
st.markdown("""
<style>
/* Bold table text (data_editor) */
div[data-testid="stDataFrame"] * {
  font-weight: 700 !important;
}
div[data-testid="stDataFrame"] thead th {
  font-weight: 900 !important;
  font-size: 15px !important;
}
div[data-testid="stDataFrame"] tbody td {
  font-size: 15px !important;
}

/* Reduce vertical padding so more content fits without scroll */
.main .block-container {
  padding-top: 0.8rem !important;
  padding-bottom: 0.8rem !important;
  max-width: 1400px !important;
}

/* Reduce margins on headers */
h1, h2, h3 {
  margin-bottom: 0.35rem !important;
}
</style>
""", unsafe_allow_html=True)


# ================== VIEWPORT HEIGHT (AUTO TABLE HEIGHT) ==================
# We store viewport height in session state, updated by a small JS snippet.
if "viewport_h" not in st.session_state:
    st.session_state.viewport_h = 900  # safe default

components.html(
    """
<script>
(function() {
  const h = window.innerHeight || 900;
  const msg = {isStreamlitMessage: true, type: "VIEWPORT_HEIGHT", height: h};
  window.parent.postMessage(msg, "*");
})();
</script>
""",
    height=0,
)

# Streamlit listens for postMessage via st.experimental_get_query_params? No.
# So we emulate it by using a hidden text input fed by query params? Not possible.
# Instead: we provide a manual fallback slider in sidebar, and compute heights off it.

# Sidebar "screen height" override (because Streamlit can't reliably receive JS postMessage)
st.sidebar.markdown("### Affichage")
st.session_state.viewport_h = st.sidebar.slider(
    "Hauteur écran (px) (ajuste pour éviter le scroll)",
    min_value=650,
    max_value=1400,
    value=int(st.session_state.viewport_h),
    step=25,
    key="vh_slider",
)
st.sidebar.caption("Ajuste une fois par ordinateur, après ça tu ne touches plus.")


def editor_height():
    """
    Compute a table height that fits comfortably on screen.
    We assume:
    - header + controls consume ~320px on each tab
    - leave a small margin
    """
    h = int(st.session_state.viewport_h)
    return max(380, min(820, h - 320))


# ================== AUTH ==================
if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.title("Accès protégé")
    pwd = st.text_input("Mot de passe", type="password", key="pwd")
    app_password = st.secrets.get("APP_PASSWORD")
    if st.button("Se connecter", key="btn_login"):
        if not app_password:
            st.error("APP_PASSWORD manquant dans Streamlit secrets.")
            st.stop()
        if pwd == app_password:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()


# ================== DENOMS ==================
DENOMS = {
    "Billet 100 $": 10000,
    "Billet 50 $": 5000,
    "Billet 20 $": 2000,
    "Billet 10 $": 1000,
    "Billet 5 $": 500,
    "Pièce 2 $": 200,
    "Pièce 1 $": 100,
    "Pièce 0,25 $": 25,
    "Pièce 0,10 $": 10,
    "Pièce 0,05 $": 5,
    "Rouleau 2 $ (25) — 50 $": 5000,
    "Rouleau 1 $ (25) — 25 $": 2500,
    "Rouleau 0,25 $ (40) — 10 $": 1000,
    "Rouleau 0,10 $ (50) — 5 $": 500,
    "Rouleau 0,05 $ (40) — 2 $": 200,
}

BILLS_BIG = ["Billet 100 $", "Billet 50 $", "Billet 20 $"]
BILLS_SMALL = ["Billet 10 $", "Billet 5 $"]
COINS = ["Pièce 2 $", "Pièce 1 $", "Pièce 0,25 $", "Pièce 0,10 $", "Pièce 0,05 $"]
ROLLS = [
    "Rouleau 2 $ (25) — 50 $",
    "Rouleau 1 $ (25) — 25 $",
    "Rouleau 0,25 $ (40) — 10 $",
    "Rouleau 0,10 $ (50) — 5 $",
    "Rouleau 0,05 $ (40) — 2 $",
]
DISPLAY_ORDER = BILLS_BIG + BILLS_SMALL + COINS + ROLLS


# ================== HELPERS ==================
def safe_int(x, default=0) -> int:
    try:
        if x is None or x == "":
            return default
        return int(x)
    except Exception:
        return default


def cents_to_str(c: int) -> str:
    return f"{c/100:.2f} $"


def total_cents(counts: dict) -> int:
    return sum(safe_int(counts.get(k, 0)) * DENOMS[k] for k in DENOMS)


def add_counts(a: dict, b: dict) -> dict:
    return {k: safe_int(a.get(k, 0)) + safe_int(b.get(k, 0)) for k in DENOMS}


def sub_counts(a: dict, b: dict) -> dict:
    return {k: safe_int(a.get(k, 0)) - safe_int(b.get(k, 0)) for k in DENOMS}


def clamp_locked(locked: dict, avail: dict) -> dict:
    out = {}
    for k, v in (locked or {}).items():
        v = safe_int(v, 0)
        if v < 0:
            v = 0
        mx = safe_int(avail.get(k, 0), 0)
        if v > mx:
            v = mx
        out[k] = v
    return out


def take_greedy(remaining: int, keys: list, avail: dict, out: dict, locked: dict) -> int:
    for k in keys:
        if remaining <= 0:
            break
        if k in locked:
            continue
        v = DENOMS[k]
        can_take = safe_int(avail.get(k, 0)) - safe_int(out.get(k, 0))
        if can_take < 0:
            can_take = 0
        take = min(remaining // v, can_take)
        if take > 0:
            out[k] = safe_int(out.get(k, 0)) + take
            remaining -= take * v
    return remaining


def suggest_withdrawal(amount_cents: int, allowed: list, avail: dict, locked: dict, priority: list):
    out = {k: 0 for k in DENOMS}
    locked = locked or {}

    for k, q in locked.items():
        out[k] = safe_int(q)

    remaining = amount_cents - total_cents(out)
    if remaining < 0:
        return out, remaining

    allowed_set = set(allowed)
    prio = [k for k in priority if k in allowed_set]
    remaining = take_greedy(remaining, prio, avail, out, locked)
    return out, remaining


def hash_payload(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def list_dates(folder: str):
    files = sorted([f for f in os.listdir(folder) if f.endswith("_state.json")])
    return [f.replace("_state.json", "") for f in files]


def save_state(folder: str, d: date, payload: dict):
    p = os.path.join(folder, f"{d.isoformat()}_state.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_state(folder: str, d: date):
    p = os.path.join(folder, f"{d.isoformat()}_state.json")
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_receipt(folder: str, d: date, html: str):
    p = os.path.join(folder, f"{d.isoformat()}_receipt.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(html)


def load_receipt(folder: str, d: date):
    p = os.path.join(folder, f"{d.isoformat()}_receipt.html")
    if not os.path.exists(p):
        return None, None
    with open(p, "r", encoding="utf-8") as f:
        return f.read(), p


def receipt_html(title: str, meta: dict, headers: list, rows: list) -> str:
    meta_html = "".join([f"<div><b>{k}:</b> {v}</div>" for k, v in meta.items()])
    thead = "".join([f"<th>{h}</th>" for h in headers])

    body = ""
    for r in rows:
        tds = []
        for i, h in enumerate(headers):
            val = r.get(h, "")
            if i == 0:
                tds.append(f"<td><b>{val}</b></td>")
            else:
                tds.append(f"<td style='text-align:center'><b>{val}</b></td>")
        body += "<tr>" + "".join(tds) + "</tr>"

    return f"""
    <html><head><meta charset="utf-8"/><title>{title}</title>
    <style>
      body{{font-family:Arial,sans-serif;padding:18px;color:#111}}
      .top{{display:flex;justify-content:space-between;gap:16px}}
      .meta{{font-size:13px;opacity:.95}}
      table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:14px}}
      th,td{{border:1px solid #222;padding:6px}}
      th{{background:#f0f0f0;font-weight:900}}
      .btnbar{{margin-top:12px}}
      button{{padding:10px 14px;border-radius:10px;border:1px solid #bbb;background:#fff;cursor:pointer;font-weight:700}}
      @media print{{.btnbar{{display:none}} body{{padding:0}}}}
    </style></head>
    <body>
      <div class="top">
        <div><h2 style="margin:0">{title}</h2><div style="opacity:.7;font-size:12px">Imprime avec le bouton.</div></div>
        <div class="meta">{meta_html}</div>
      </div>
      <div class="btnbar"><button onclick="window.print()">🖨️ Imprimer</button></div>
      <table>
        <thead><tr>{thead}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </body></html>
    """


# ================== DEFAULT TABLES ==================
def df_caisse_default():
    return pd.DataFrame([{"Dénomination": k, "OPEN": 0, "CLOSE": 0, "RETRAIT": 0, "RESTANT": 0} for k in DISPLAY_ORDER])


def df_boite_default():
    return pd.DataFrame([{
        "Dénomination": k,
        "Boîte (avant)": 0,
        "Dépôt": 0,
        "Change retiré": 0,
        "Boîte (après)": 0
    } for k in DISPLAY_ORDER])


# ================== NORMALISATION ==================
def normalize_caisse_df_load(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df_caisse_default()
    df = df.copy()
    if "Dénomination" not in df.columns:
        return df_caisse_default()
    for col in ["OPEN", "CLOSE", "RETRAIT", "RESTANT"]:
        if col not in df.columns:
            df[col] = 0
    df = df[["Dénomination", "OPEN", "CLOSE", "RETRAIT", "RESTANT"]]
    df["Dénomination"] = df["Dénomination"].astype(str)
    df = df.set_index("Dénomination").reindex(DISPLAY_ORDER).fillna(0).reset_index()
    for col in ["OPEN", "CLOSE", "RETRAIT", "RESTANT"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    return df


def normalize_boite_df_load(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df_boite_default()
    df = df.copy()
    if "Dépôt" not in df.columns and "Dépôt billets" in df.columns:
        df.rename(columns={"Dépôt billets": "Dépôt"}, inplace=True)
    if "Dénomination" not in df.columns:
        return df_boite_default()
    for col in ["Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"]:
        if col not in df.columns:
            df[col] = 0
    df = df[["Dénomination", "Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"]]
    df["Dénomination"] = df["Dénomination"].astype(str)
    df = df.set_index("Dénomination").reindex(DISPLAY_ORDER).fillna(0).reset_index()
    for col in ["Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    return df


def normalize_caisse_df_live(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df_caisse_default()
    df = df.copy()
    if "Dénomination" not in df.columns:
        return df_caisse_default()
    for col in ["OPEN", "CLOSE", "RETRAIT", "RESTANT"]:
        if col not in df.columns:
            df[col] = 0
    for col in ["OPEN", "CLOSE", "RETRAIT", "RESTANT"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["Dénomination"] = df["Dénomination"].astype(str)
    return df


def normalize_boite_df_live(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df_boite_default()
    df = df.copy()
    if "Dépôt" not in df.columns and "Dépôt billets" in df.columns:
        df.rename(columns={"Dépôt billets": "Dépôt"}, inplace=True)
    if "Dénomination" not in df.columns:
        return df_boite_default()
    for col in ["Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"]:
        if col not in df.columns:
            df[col] = 0
    for col in ["Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["Dénomination"] = df["Dénomination"].astype(str)
    return df


# ================== STATE INIT ==================
today = datetime.now(TZ).date()

def ensure_state():
    defaults = {
        "booted_for": None,
        "cashier": "",
        "register_no": 1,
        "target_dollars": 200,
        "df_caisse": df_caisse_default(),
        "df_boite": df_boite_default(),
        "locked_retrait_caisse": {},
        "locked_withdraw_boite": {},
        "boite_allowed": set(["Billet 20 $", "Billet 10 $", "Billet 5 $"] + COINS + ROLLS),
        "last_hash_caisse": None,
        "last_hash_boite": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

ensure_state()

if st.session_state.booted_for != today:
    st.session_state.booted_for = today

    sc = load_state(DIR_CAISSE, today)
    if sc:
        st.session_state.cashier = sc.get("meta", {}).get("Caissier(ère)", st.session_state.cashier)
        st.session_state.register_no = safe_int(sc.get("meta", {}).get("Caisse #", st.session_state.register_no), 1)
        st.session_state.target_dollars = safe_int(sc.get("meta", {}).get("Cible $", st.session_state.target_dollars), 200)
        table = sc.get("register", {}).get("table")
        if isinstance(table, list) and table:
            st.session_state.df_caisse = normalize_caisse_df_load(pd.DataFrame(table))
        st.session_state.locked_retrait_caisse = sc.get("register", {}).get("locked_retrait", {}) or {}

    sb = load_state(DIR_BOITE, today)
    if sb:
        st.session_state.cashier = sb.get("meta", {}).get("Caissier(ère)", st.session_state.cashier)
        table = sb.get("boite", {}).get("table")
        if isinstance(table, list) and table:
            st.session_state.df_boite = normalize_boite_df_load(pd.DataFrame(table))
        st.session_state.locked_withdraw_boite = sb.get("boite", {}).get("locked_withdraw", {}) or {}
        allowed = sb.get("boite", {}).get("allowed_denoms")
        if isinstance(allowed, list) and allowed:
            st.session_state.boite_allowed = set(allowed)

st.session_state.df_caisse = normalize_caisse_df_live(st.session_state.df_caisse)
st.session_state.df_boite = normalize_boite_df_live(st.session_state.df_boite)


# ================== HEADER ==================
st.title("Registre — Caisse & Boîte de monnaie")

h1, h2, h3, h4, h5 = st.columns([1.2, 1.0, 1.2, 1.8, 2.0])
with h1:
    st.write("**Date:**", today.isoformat())
with h2:
    st.write("**Heure:**", datetime.now(TZ).strftime("%H:%M"))
with h3:
    st.session_state.register_no = st.selectbox(
        "Caisse #", [1, 2, 3],
        index=[1, 2, 3].index(st.session_state.register_no),
        key="sel_register",
    )
with h4:
    st.session_state.cashier = st.text_input("Caissier(ère)", value=st.session_state.cashier, key="txt_cashier")
with h5:
    st.session_state.target_dollars = st.number_input(
        "Cible à laisser ($)", min_value=0, step=10,
        value=int(st.session_state.target_dollars),
        key="num_target",
    )

st.divider()
tab_caisse, tab_boite, tab_save = st.tabs(["1) Caisse", "2) Boîte (Échange)", "3) Sauvegarde & reçus"])


# ================== TAB: CAISSE ==================
with tab_caisse:
    st.subheader("Caisse — OPEN/CLOSE + RETRAIT + RESTANT (même tableau)")

    edited = st.data_editor(
        st.session_state.df_caisse,
        hide_index=True,
        use_container_width=True,
        height=editor_height(),
        column_config={
            "Dénomination": st.column_config.TextColumn(disabled=True),
            "OPEN": st.column_config.NumberColumn(min_value=0, step=1),
            "CLOSE": st.column_config.NumberColumn(min_value=0, step=1),
            "RETRAIT": st.column_config.NumberColumn(disabled=True),
            "RESTANT": st.column_config.NumberColumn(disabled=True),
        },
        key="ed_caisse",
    )
    st.session_state.df_caisse = normalize_caisse_df_live(edited)

    open_counts = {r["Dénomination"]: safe_int(r["OPEN"]) for _, r in st.session_state.df_caisse.iterrows()}
    close_counts = {r["Dénomination"]: safe_int(r["CLOSE"]) for _, r in st.session_state.df_caisse.iterrows()}

    total_open = total_cents(open_counts)
    total_close = total_cents(close_counts)
    TARGET = int(st.session_state.target_dollars) * 100
    diff = total_close - TARGET

    a, b, c = st.columns(3)
    a.info("TOTAL OPEN: " + cents_to_str(total_open))
    b.success("TOTAL CLOSE: " + cents_to_str(total_close))
    c.write("**À retirer:** " + f"**{cents_to_str(diff)}**")

    retrait = {k: 0 for k in DENOMS}
    restant = dict(close_counts)
    remaining = 0

    if diff > 0:
        coins_desc = sorted(COINS, key=lambda x: DENOMS[x], reverse=True)
        rolls_desc = sorted(ROLLS, key=lambda x: DENOMS[x], reverse=True)
        priority_caisse = BILLS_BIG + BILLS_SMALL + coins_desc + rolls_desc

        st.session_state.locked_retrait_caisse = clamp_locked(st.session_state.locked_retrait_caisse, close_counts)
        retrait, remaining = suggest_withdrawal(
            diff,
            allowed=DISPLAY_ORDER,
            avail=close_counts,
            locked=dict(st.session_state.locked_retrait_caisse),
            priority=priority_caisse,
        )
        restant = sub_counts(close_counts, retrait)

    df = st.session_state.df_caisse.copy()
    df["RETRAIT"] = df["Dénomination"].map(lambda k: safe_int(retrait.get(k, 0)))
    df["RESTANT"] = df["Dénomination"].map(lambda k: safe_int(restant.get(k, 0)))
    st.session_state.df_caisse = normalize_caisse_df_live(df)

    st.divider()
    st.subheader("Ajuster RETRAIT (➖/➕) — aligné sur les lignes")

    if diff <= 0:
        st.warning("Sous la cible (ou égal). Aucun retrait nécessaire.")
    else:
        if remaining == 0:
            st.success("Retrait proposé: " + cents_to_str(total_cents(retrait)))
        elif remaining < 0:
            st.warning("Verrouillage trop haut. Dépasse de " + cents_to_str(-remaining))
        else:
            st.warning("Impossible exact. Reste: " + cents_to_str(remaining))

        r0, r1 = st.columns([1.3, 3.7])
        with r0:
            if st.button("Reset ajustements retrait", key="btn_reset_caisse_locks"):
                st.session_state.locked_retrait_caisse = {}
                st.rerun()
        with r1:
            st.caption("Chaque ➖/➕ verrouille la dénomination et recalcule le reste automatiquement.")

        for k in DISPLAY_ORDER:
            mx = safe_int(close_counts.get(k, 0))
            q = safe_int(retrait.get(k, 0))
            c0, c1, c2, c3, c4 = st.columns([2.6, 0.7, 1.0, 0.7, 1.0], vertical_alignment="center")
            c0.markdown(f"**{k}**")
            minus = c1.button("➖", key=f"caisse_minus_inline_{k}")
            c2.markdown(f"**{q}**")
            plus = c3.button("➕", key=f"caisse_plus_inline_{k}")
            c4.caption(f"Dispo: {mx}")
            if minus or plus:
                new_locked = dict(st.session_state.locked_retrait_caisse)
                if k not in new_locked:
                    new_locked[k] = q
                if minus:
                    new_locked[k] = max(0, safe_int(new_locked[k]) - 1)
                if plus:
                    new_locked[k] = min(mx, safe_int(new_locked[k]) + 1)
                st.session_state.locked_retrait_caisse = new_locked
                st.rerun()

    # Receipt rows
    rows_caisse = []
    for k in DISPLAY_ORDER:
        rows_caisse.append({
            "Dénomination": k,
            "OPEN": safe_int(open_counts.get(k, 0)),
            "CLOSE": safe_int(close_counts.get(k, 0)),
            "RETRAIT": safe_int(retrait.get(k, 0)),
            "RESTANT": safe_int(restant.get(k, 0)),
        })
    rows_caisse.append({
        "Dénomination": "TOTAL ($)",
        "OPEN": f"{total_open/100:.2f}",
        "CLOSE": f"{total_close/100:.2f}",
        "RETRAIT": f"{total_cents(retrait)/100:.2f}",
        "RESTANT": f"{total_cents(restant)/100:.2f}",
    })

    meta_caisse = {
        "Type": "CAISSE",
        "Date": today.isoformat(),
        "Généré à": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "Caisse #": int(st.session_state.register_no),
        "Caissier(ère)": (st.session_state.cashier.strip() or "—"),
        "Cible $": int(st.session_state.target_dollars),
    }

    payload_caisse = {
        "meta": meta_caisse,
        "register": {
            "table": st.session_state.df_caisse.to_dict("records"),
            "locked_retrait": st.session_state.locked_retrait_caisse,
            "rows": rows_caisse,
        },
    }

    hc = hash_payload(payload_caisse)
    if st.session_state.last_hash_caisse != hc:
        html = receipt_html("Reçu — Caisse", meta_caisse, ["Dénomination", "OPEN", "CLOSE", "RETRAIT", "RESTANT"], rows_caisse)
        save_state(DIR_CAISSE, today, payload_caisse)
        save_receipt(DIR_CAISSE, today, html)
        st.session_state.last_hash_caisse = hc

    st.markdown("### Aperçu reçu — Caisse")
    components.html(
        receipt_html("Reçu — Caisse", meta_caisse, ["Dénomination", "OPEN", "CLOSE", "RETRAIT", "RESTANT"], rows_caisse),
        height=620,
        scrolling=True,
    )


# ================== TAB: BOÎTE ==================
with tab_boite:
    st.subheader("Boîte (Échange) — Boîte (avant) + Dépôt + Change retiré + Boîte (après) (même tableau)")

    with st.expander("⚙️ Types autorisés pour le change", expanded=True):
        allowed = set(st.session_state.boite_allowed)
        c1, c2, c3 = st.columns(3)
        cols = [c1, c2, c3]
        for i, k in enumerate(DISPLAY_ORDER):
            with cols[i % 3]:
                checked = k in allowed
                if st.checkbox(k, value=checked, key=f"allow_boite_{k}"):
                    allowed.add(k)
                else:
                    allowed.discard(k)
        if not allowed:
            st.warning("Choisis au moins un type autorisé.")
        st.session_state.boite_allowed = allowed

    edited_b = st.data_editor(
        st.session_state.df_boite,
        hide_index=True,
        use_container_width=True,
        height=editor_height(),
        column_config={
            "Dénomination": st.column_config.TextColumn(disabled=True),
            "Boîte (avant)": st.column_config.NumberColumn(min_value=0, step=1),
            "Dépôt": st.column_config.NumberColumn(min_value=0, step=1),
            "Change retiré": st.column_config.NumberColumn(disabled=True),
            "Boîte (après)": st.column_config.NumberColumn(disabled=True),
        },
        key="ed_boite",
    )
    st.session_state.df_boite = normalize_boite_df_live(edited_b)

    dfb = st.session_state.df_boite
    box_before = {r["Dénomination"]: safe_int(r["Boîte (avant)"]) for _, r in dfb.iterrows()}
    deposit = {r["Dénomination"]: safe_int(r["Dépôt"]) for _, r in dfb.iterrows()}

    total_before = total_cents(box_before)
    total_deposit = total_cents(deposit)

    s1, s2, s3 = st.columns(3)
    s1.info("Boîte (avant): " + cents_to_str(total_before))
    s2.success("Dépôt total: " + cents_to_str(total_deposit))
    s3.write("**Change objectif:** " + f"**{cents_to_str(total_deposit)}**")

    box_after_deposit = add_counts(box_before, deposit)

    priority_boite = (
        ["Billet 20 $", "Billet 10 $", "Billet 5 $"]
        + ["Pièce 2 $", "Pièce 1 $", "Pièce 0,25 $", "Pièce 0,10 $", "Pièce 0,05 $"]
        + ROLLS
        + ["Billet 50 $", "Billet 100 $"]
    )

    withdraw = {k: 0 for k in DENOMS}
    box_after = dict(box_after_deposit)
    remaining = 0

    if total_deposit > 0 and st.session_state.boite_allowed:
        st.session_state.locked_withdraw_boite = clamp_locked(st.session_state.locked_withdraw_boite, box_after_deposit)
        withdraw, remaining = suggest_withdrawal(
            total_deposit,
            allowed=list(st.session_state.boite_allowed),
            avail=box_after_deposit,
            locked=dict(st.session_state.locked_withdraw_boite),
            priority=priority_boite,
        )
        box_after = sub_counts(box_after_deposit, withdraw)

    df = st.session_state.df_boite.copy()
    df["Change retiré"] = df["Dénomination"].map(lambda k: safe_int(withdraw.get(k, 0)))
    df["Boîte (après)"] = df["Dénomination"].map(lambda k: safe_int(box_after.get(k, 0)))
    st.session_state.df_boite = normalize_boite_df_live(df)

    st.divider()
    st.subheader("Ajuster Change retiré (➖/➕) — aligné sur les lignes")

    if total_deposit == 0:
        st.warning("Dépôt = 0. Rien à calculer.")
    elif not st.session_state.boite_allowed:
        st.error("Aucun type autorisé.")
    else:
        if remaining == 0:
            st.success("Change retiré: " + cents_to_str(total_cents(withdraw)))
        elif remaining < 0:
            st.warning("Verrouillage trop haut. Dépasse de " + cents_to_str(-remaining))
        else:
            st.warning("Impossible exact. Reste: " + cents_to_str(remaining))

        r0, r1 = st.columns([1.3, 3.7])
        with r0:
            if st.button("Reset ajustements (boîte)", key="btn_reset_boite_locks"):
                st.session_state.locked_withdraw_boite = {}
                st.rerun()
        with r1:
            st.caption("Chaque ➖/➕ verrouille la dénomination et recalcule le reste automatiquement.")

        allowed_sorted = [k for k in DISPLAY_ORDER if k in st.session_state.boite_allowed]
        for k in allowed_sorted:
            mx = safe_int(box_after_deposit.get(k, 0))
            q = safe_int(withdraw.get(k, 0))
            c0, c1, c2, c3, c4 = st.columns([2.6, 0.7, 1.0, 0.7, 1.0], vertical_alignment="center")
            c0.markdown(f"**{k}**")
            minus = c1.button("➖", key=f"boite_minus_inline_{k}")
            c2.markdown(f"**{q}**")
            plus = c3.button("➕", key=f"boite_plus_inline_{k}")
            c4.caption(f"Dispo: {mx}")
            if minus or plus:
                new_locked = dict(st.session_state.locked_withdraw_boite)
                if k not in new_locked:
                    new_locked[k] = q
                if minus:
                    new_locked[k] = max(0, safe_int(new_locked[k]) - 1)
                if plus:
                    new_locked[k] = min(mx, safe_int(new_locked[k]) + 1)
                st.session_state.locked_withdraw_boite = new_locked
                st.rerun()

    rows_boite = []
    for k in DISPLAY_ORDER:
        rows_boite.append({
            "Dénomination": k,
            "Boîte (avant)": safe_int(box_before.get(k, 0)),
            "Dépôt": safe_int(deposit.get(k, 0)),
            "Change retiré": safe_int(withdraw.get(k, 0)),
            "Boîte (après)": safe_int(box_after.get(k, 0)),
        })
    rows_boite.append({
        "Dénomination": "TOTAL ($)",
        "Boîte (avant)": f"{total_before/100:.2f}",
        "Dépôt": f"{total_deposit/100:.2f}",
        "Change retiré": f"{total_cents(withdraw)/100:.2f}",
        "Boîte (après)": f"{total_cents(box_after)/100:.2f}",
    })

    meta_boite = {
        "Type": "BOÎTE (ÉCHANGE)",
        "Date": today.isoformat(),
        "Généré à": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "Caissier(ère)": (st.session_state.cashier.strip() or "—"),
        "Caisse #": int(st.session_state.register_no),
        "Dépôt total ($)": f"{total_deposit/100:.2f}",
    }

    payload_boite = {
        "meta": meta_boite,
        "boite": {
            "table": st.session_state.df_boite.to_dict("records"),
            "locked_withdraw": st.session_state.locked_withdraw_boite,
            "allowed_denoms": sorted(list(st.session_state.boite_allowed)),
            "rows": rows_boite,
        },
    }

    hb = hash_payload(payload_boite)
    if st.session_state.last_hash_boite != hb:
        html = receipt_html(
            "Reçu — Boîte (Échange)",
            meta_boite,
            ["Dénomination", "Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"],
            rows_boite,
        )
        save_state(DIR_BOITE, today, payload_boite)
        save_receipt(DIR_BOITE, today, html)
        st.session_state.last_hash_boite = hb

    st.markdown("### Aperçu reçu — Boîte (Échange)")
    components.html(
        receipt_html(
            "Reçu — Boîte (Échange)",
            meta_boite,
            ["Dénomination", "Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"],
            rows_boite,
        ),
        height=620,
        scrolling=True,
    )


# ================== TAB: SAUVEGARDE ==================
with tab_save:
    st.subheader("Sauvegarde & reçus")
    st.caption("Liste simple. Caisse et Boîte sont séparés.")

    colA, colB = st.columns(2)

    with colA:
        st.markdown("## 📒 Caisse")
        dates = list_dates(DIR_CAISSE)
        if not dates:
            st.info("Aucun enregistrement Caisse.")
        else:
            for ds in reversed(dates):
                d = date.fromisoformat(ds)
                with st.expander(f"{ds} — Reçu Caisse", expanded=False):
                    html, _ = load_receipt(DIR_CAISSE, d)
                    rp = os.path.join(DIR_CAISSE, f"{ds}_receipt.html")
                    sp = os.path.join(DIR_CAISSE, f"{ds}_state.json")
                    if html:
                        components.html(html, height=640, scrolling=True)
                    else:
                        st.warning("Reçu introuvable pour cette date.")
                    if os.path.exists(rp):
                        with open(rp, "rb") as f:
                            st.download_button("⬇️ Télécharger reçu (HTML)", f.read(), os.path.basename(rp), "text/html", key=f"dl_caisse_html_{ds}")
                    if os.path.exists(sp):
                        with open(sp, "rb") as f:
                            st.download_button("⬇️ Télécharger état (JSON)", f.read(), os.path.basename(sp), "application/json", key=f"dl_caisse_json_{ds}")

    with colB:
        st.markdown("## 🪙 Boîte (Échange)")
        dates = list_dates(DIR_BOITE)
        if not dates:
            st.info("Aucun enregistrement Boîte.")
        else:
            for ds in reversed(dates):
                d = date.fromisoformat(ds)
                with st.expander(f"{ds} — Reçu Boîte (Échange)", expanded=False):
                    html, _ = load_receipt(DIR_BOITE, d)
                    rp = os.path.join(DIR_BOITE, f"{ds}_receipt.html")
                    sp = os.path.join(DIR_BOITE, f"{ds}_state.json")
                    if html:
                        components.html(html, height=640, scrolling=True)
                    else:
                        st.warning("Reçu introuvable pour cette date.")
                    if os.path.exists(rp):
                        with open(rp, "rb") as f:
                            st.download_button("⬇️ Télécharger reçu (HTML)", f.read(), os.path.basename(rp), "text/html", key=f"dl_boite_html_{ds}")
                    if os.path.exists(sp):
                        with open(sp, "rb") as f:
                            st.download_button("⬇️ Télécharger état (JSON)", f.read(), os.path.basename(sp), "application/json", key=f"dl_boite_json_{ds}")
