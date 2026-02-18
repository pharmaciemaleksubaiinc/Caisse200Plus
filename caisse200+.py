# caisse200+.py
# Registre — Caisse & Boîte (Échange)
#
# FIXES / FEATURES:
# - Fix "enter resets value to 0" in st.data_editor:
#   * never reindex/rebuild df during live editing
#   * preserve user's typed values; only coerce numeric gently
#   * compute-only columns updated without touching editable cols
#
# - After login: choose mode
#   1) Ouverture normale
#   2) Ouverture non effectuée (si CLOSE d'hier absent) => enter CLOSE hier, calc RETRAIT hier, OPEN aujourd'hui = RESTANT hier
#
# - Caisse table: OPEN/CLOSE/RETRAIT/RESTANT on one line
# - Totals displayed UNDER each column (OPEN total, CLOSE total, À retirer, RESTANT total)
# - Inline +/- controls for RETRAIT (Caisse) and Change retiré (Boîte)
# - Boîte: choose allowed denominations for change; separate receipt + folder
# - Sauvegarde tab: date list -> expands to receipt + downloads

import os
import json
import hashlib
from datetime import datetime, date, timedelta
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

# ================== STYLE ==================
st.markdown(
    """
<style>
div[data-testid="stDataFrame"] * { font-weight: 780 !important; }
div[data-testid="stDataFrame"] thead th { font-weight: 950 !important; font-size: 15px !important; }
div[data-testid="stDataFrame"] tbody td { font-size: 15px !important; }

.main .block-container { padding-top: 0.8rem !important; padding-bottom: 0.8rem !important; max-width: 1500px !important; }
h1,h2,h3 { margin-bottom: 0.35rem !important; }

.totals-label { font-weight: 900; font-size: 14px; opacity: 0.75; margin-bottom: 6px; }
.totals-box { font-weight: 950; font-size: 18px; padding: 12px; border-radius: 14px; background: rgba(0,0,0,0.04); text-align:center; }
</style>
""",
    unsafe_allow_html=True,
)


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
def safe_int(x, default=None):
    """
    Gentle int conversion:
    - returns default if empty or invalid
    - default=None used to preserve previous values
    """
    try:
        if x is None:
            return default
        if isinstance(x, float) and pd.isna(x):
            return default
        if x == "":
            return default
        return int(x)
    except Exception:
        return default


def cents_to_str(c: int) -> str:
    return f"{c/100:.2f} $"


def total_cents(counts: dict) -> int:
    return sum(int(counts.get(k, 0)) * DENOMS[k] for k in DENOMS)


def add_counts(a: dict, b: dict) -> dict:
    return {k: int(a.get(k, 0)) + int(b.get(k, 0)) for k in DENOMS}


def sub_counts(a: dict, b: dict) -> dict:
    return {k: int(a.get(k, 0)) - int(b.get(k, 0)) for k in DENOMS}


def clamp_locked(locked: dict, avail: dict) -> dict:
    out = {}
    for k, v in (locked or {}).items():
        v = int(v)
        if v < 0:
            v = 0
        mx = int(avail.get(k, 0))
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
        can_take = int(avail.get(k, 0)) - int(out.get(k, 0))
        if can_take < 0:
            can_take = 0
        take = min(remaining // v, can_take)
        if take > 0:
            out[k] = int(out.get(k, 0)) + int(take)
            remaining -= int(take) * v
    return remaining


def suggest_withdrawal(amount_cents: int, allowed: list, avail: dict, locked: dict, priority: list):
    out = {k: 0 for k in DENOMS}
    for k, q in (locked or {}).items():
        out[k] = int(q)

    remaining = amount_cents - total_cents(out)
    if remaining < 0:
        return out, remaining

    allowed_set = set(allowed)
    prio = [k for k in priority if k in allowed_set]

    remaining = take_greedy(remaining, prio, avail, out, locked or {})
    return out, remaining


def hash_payload(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def save_json(path: str, payload: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_receipt(path: str, html: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def load_text(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def list_saved_dates(folder: str):
    files = sorted([f for f in os.listdir(folder) if f.endswith("_state.json")])
    return [f.replace("_state.json", "") for f in files]


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


# ================== DATAFRAME BUILDERS ==================
CAISSE_COLS = ["Dénomination", "OPEN", "CLOSE", "RETRAIT", "RESTANT"]
BOITE_COLS = ["Dénomination", "Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"]


def df_caisse_default():
    return pd.DataFrame([{ "Dénomination": k, "OPEN": 0, "CLOSE": 0, "RETRAIT": 0, "RESTANT": 0 } for k in DISPLAY_ORDER])


def df_boite_default():
    return pd.DataFrame([{ "Dénomination": k, "Boîte (avant)": 0, "Dépôt": 0, "Change retiré": 0, "Boîte (après)": 0 } for k in DISPLAY_ORDER])


def gentle_coerce_editable(df_out: pd.DataFrame, df_prev: pd.DataFrame, editable_cols: list):
    """
    Key fix: preserve user edits.
    If user typed blank/invalid and hit Enter, we keep previous value instead of forcing 0.
    """
    df = df_out.copy()
    prev = df_prev.copy()

    # Ensure denom alignment (never reindex during edit)
    if "Dénomination" not in df.columns or "Dénomination" not in prev.columns:
        return df

    denom_to_prev = {r["Dénomination"]: r for _, r in prev.iterrows()}

    for idx, row in df.iterrows():
        d = row["Dénomination"]
        prev_row = denom_to_prev.get(d, {})
        for col in editable_cols:
            new_v = safe_int(row.get(col), default=None)
            if new_v is None:
                df.at[idx, col] = int(prev_row.get(col, 0))
            else:
                df.at[idx, col] = int(new_v)

    return df


# ================== SAVING HELPERS ==================
def caisse_paths(d: date):
    ds = d.isoformat()
    return (
        os.path.join(DIR_CAISSE, f"{ds}_state.json"),
        os.path.join(DIR_CAISSE, f"{ds}_receipt.html"),
    )

def boite_paths(d: date):
    ds = d.isoformat()
    return (
        os.path.join(DIR_BOITE, f"{ds}_state.json"),
        os.path.join(DIR_BOITE, f"{ds}_receipt.html"),
    )


# ================== AUTH ==================
if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.title("Accès protégé")
    pwd = st.text_input("Mot de passe", type="password", key="pwd")
    if st.button("Se connecter", key="login_btn"):
        if pwd == st.secrets.get("APP_PASSWORD"):
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()


# ================== MODE PICKER ==================
today = datetime.now(TZ).date()
yesterday = today - timedelta(days=1)

if "mode_pick" not in st.session_state:
    st.session_state.mode_pick = None  # "normal" or "missed_close"
if "mode_done_today" not in st.session_state:
    st.session_state.mode_done_today = None  # store date string so it doesn't ask again today

if st.session_state.mode_done_today != today.isoformat():
    st.session_state.mode_pick = None

if st.session_state.mode_pick is None:
    st.title("Choix d'ouverture")
    st.caption("Sélectionne le scénario d'aujourd'hui.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("✅ Ouverture normale", use_container_width=True, key="pick_normal"):
            st.session_state.mode_pick = "normal"
            st.session_state.mode_done_today = today.isoformat()
            st.rerun()
    with c2:
        if st.button("⚠️ Ouverture non effectuée (hier)", use_container_width=True, key="pick_missed"):
            st.session_state.mode_pick = "missed_close"
            st.session_state.mode_done_today = today.isoformat()
            st.rerun()
    st.stop()


# ================== STATE INIT ==================
def ensure_state():
    defaults = {
        "cashier": "",
        "register_no": 1,
        "target_dollars": 200,

        "df_caisse_today": df_caisse_default(),
        "df_caisse_yesterday": df_caisse_default(),  # used only in missed_close mode (as CLOSE hier)
        "locked_retrait_caisse": {},

        "df_boite": df_boite_default(),
        "locked_withdraw_boite": {},
        "boite_allowed": set(["Billet 20 $", "Billet 10 $", "Billet 5 $"] + COINS + ROLLS),

        "last_hash_caisse": None,
        "last_hash_boite": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

ensure_state()


# ================== LOAD TODAY SAVES ==================
# (Load once per day)
if "booted_for" not in st.session_state:
    st.session_state.booted_for = None

if st.session_state.booted_for != today.isoformat():
    st.session_state.booted_for = today.isoformat()

    state_path_c, _ = caisse_paths(today)
    saved_c = load_json(state_path_c)
    if saved_c:
        st.session_state.cashier = saved_c.get("meta", {}).get("Caissier(ère)", st.session_state.cashier)
        st.session_state.register_no = int(saved_c.get("meta", {}).get("Caisse #", st.session_state.register_no))
        st.session_state.target_dollars = int(saved_c.get("meta", {}).get("Cible $", st.session_state.target_dollars))
        tbl = saved_c.get("register", {}).get("table_today", None)
        if isinstance(tbl, list) and tbl:
            df = pd.DataFrame(tbl)
            st.session_state.df_caisse_today = df[CAISSE_COLS].copy()
        st.session_state.locked_retrait_caisse = saved_c.get("register", {}).get("locked_retrait", {}) or {}

        # If missed mode, we may have yesterday table saved too
        tbl_y = saved_c.get("register", {}).get("table_yesterday", None)
        if isinstance(tbl_y, list) and tbl_y:
            dfy = pd.DataFrame(tbl_y)
            st.session_state.df_caisse_yesterday = dfy[CAISSE_COLS].copy()

    state_path_b, _ = boite_paths(today)
    saved_b = load_json(state_path_b)
    if saved_b:
        tbl = saved_b.get("boite", {}).get("table", None)
        if isinstance(tbl, list) and tbl:
            df = pd.DataFrame(tbl)
            st.session_state.df_boite = df[BOITE_COLS].copy()
        st.session_state.locked_withdraw_boite = saved_b.get("boite", {}).get("locked_withdraw", {}) or {}
        allowed = saved_b.get("boite", {}).get("allowed_denoms")
        if isinstance(allowed, list) and allowed:
            st.session_state.boite_allowed = set(allowed)


# ================== HEADER ==================
st.title("Registre — Caisse & Boîte de monnaie")

h1, h2, h3, h4 = st.columns([1.1, 1.0, 1.2, 2.0])
with h1:
    st.write("**Date:**", today.isoformat())
with h2:
    st.write("**Heure:**", datetime.now(TZ).strftime("%H:%M"))
with h3:
    st.session_state.register_no = st.selectbox("Caisse #", [1, 2, 3], index=[1,2,3].index(st.session_state.register_no), key="reg_sel")
with h4:
    st.session_state.cashier = st.text_input("Caissier(ère)", value=st.session_state.cashier, key="cashier_txt")

st.session_state.target_dollars = st.number_input("Cible à laisser ($)", min_value=0, step=10, value=int(st.session_state.target_dollars), key="target_num")

st.divider()
tab_caisse, tab_boite, tab_save = st.tabs(["1) Caisse", "2) Boîte (Échange)", "3) Sauvegarde & reçus"])


# ================== TAB: CAISSE ==================
with tab_caisse:
    TARGET = int(st.session_state.target_dollars) * 100

    # PRIORITY (caisse): favour big bills to reach target
    coins_desc = sorted(COINS, key=lambda x: DENOMS[x], reverse=True)
    rolls_desc = sorted(ROLLS, key=lambda x: DENOMS[x], reverse=True)
    PRIORITY_CAISSE = BILLS_BIG + BILLS_SMALL + coins_desc + rolls_desc

    st.subheader("Caisse")

    # If missed close mode: show yesterday CLOSE input first
    if st.session_state.mode_pick == "missed_close":
        st.markdown("### ⚠️ Hier — fermeture non effectuée")
        st.caption("Entre le comptage de fin de journée d'hier (CLOSE). L'app calcule le retrait et prépare l'OPEN d'aujourd'hui.")

        prev_y = st.session_state.df_caisse_yesterday.copy()
        edited_y = st.data_editor(
            st.session_state.df_caisse_yesterday,
            hide_index=True,
            use_container_width=True,
            height=520,
            column_config={
                "Dénomination": st.column_config.TextColumn(disabled=True),
                "OPEN": st.column_config.NumberColumn(disabled=True),
                "CLOSE": st.column_config.NumberColumn(min_value=0, step=1),
                "RETRAIT": st.column_config.NumberColumn(disabled=True),
                "RESTANT": st.column_config.NumberColumn(disabled=True),
            },
            key="ed_caisse_yesterday",
        )

        # Gentle coerce ONLY CLOSE (editable) to prevent reset bug
        edited_y = gentle_coerce_editable(edited_y, prev_y, editable_cols=["CLOSE"])
        st.session_state.df_caisse_yesterday = edited_y

        close_y = {r["Dénomination"]: int(r["CLOSE"]) for _, r in st.session_state.df_caisse_yesterday.iterrows()}
        total_close_y = total_cents(close_y)
        diff_y = total_close_y - TARGET

        retrait_y = {k: 0 for k in DENOMS}
        restant_y = dict(close_y)
        remaining_y = 0

        if diff_y > 0:
            st.session_state.locked_retrait_caisse = clamp_locked(st.session_state.locked_retrait_caisse, close_y)
            retrait_y, remaining_y = suggest_withdrawal(
                diff_y,
                allowed=DISPLAY_ORDER,
                avail=close_y,
                locked=dict(st.session_state.locked_retrait_caisse),
                priority=PRIORITY_CAISSE,
            )
            restant_y = sub_counts(close_y, retrait_y)

        # Write computed columns without touching CLOSE
        dfy = st.session_state.df_caisse_yesterday.copy()
        dfy["RETRAIT"] = dfy["Dénomination"].map(lambda k: int(retrait_y.get(k, 0)))
        dfy["RESTANT"] = dfy["Dénomination"].map(lambda k: int(restant_y.get(k, 0)))
        st.session_state.df_caisse_yesterday = dfy

        # Totals under columns (yesterday)
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            st.markdown("<div class='totals-label'>TOTAL OPEN (hier)</div><div class='totals-box'>—</div>", unsafe_allow_html=True)
        with f2:
            st.markdown(f"<div class='totals-label'>TOTAL CLOSE (hier)</div><div class='totals-box'>{cents_to_str(total_close_y)}</div>", unsafe_allow_html=True)
        with f3:
            st.markdown(f"<div class='totals-label'>À retirer (hier)</div><div class='totals-box'>{cents_to_str(diff_y)}</div>", unsafe_allow_html=True)
        with f4:
            st.markdown(f"<div class='totals-label'>TOTAL RESTANT (hier)</div><div class='totals-box'>{cents_to_str(total_cents(restant_y))}</div>", unsafe_allow_html=True)

        st.divider()
        st.markdown("### Ajuster RETRAIT (hier) (➖/➕)")

        if diff_y <= 0:
            st.warning("Hier: sous la cible (ou égal). Aucun retrait.")
        else:
            if remaining_y == 0:
                st.success("Retrait hier proposé: " + cents_to_str(total_cents(retrait_y)))
            elif remaining_y < 0:
                st.warning("Verrouillage trop haut. Dépasse de " + cents_to_str(-remaining_y))
            else:
                st.warning("Impossible exact. Reste: " + cents_to_str(remaining_y))

            r0, r1 = st.columns([1.3, 3.7])
            with r0:
                if st.button("Reset ajustements retrait (hier)", key="btn_reset_y"):
                    st.session_state.locked_retrait_caisse = {}
                    st.rerun()
            with r1:
                st.caption("➖/➕ verrouille la dénomination et recalcule le reste.")

            for k in DISPLAY_ORDER:
                mx = int(close_y.get(k, 0))
                q = int(retrait_y.get(k, 0))
                c0, c1, c2, c3, c4 = st.columns([2.6, 0.7, 1.0, 0.7, 1.0], vertical_alignment="center")
                c0.markdown(f"**{k}**")
                minus = c1.button("➖", key=f"y_minus_{k}")
                c2.markdown(f"**{q}**")
                plus = c3.button("➕", key=f"y_plus_{k}")
                c4.caption(f"Dispo: {mx}")
                if minus or plus:
                    new_locked = dict(st.session_state.locked_retrait_caisse)
                    if k not in new_locked:
                        new_locked[k] = q
                    if minus:
                        new_locked[k] = max(0, int(new_locked[k]) - 1)
                    if plus:
                        new_locked[k] = min(mx, int(new_locked[k]) + 1)
                    st.session_state.locked_retrait_caisse = new_locked
                    st.rerun()

        # OPEN today should be yesterday restant
        # We populate today's OPEN column with restant_y, but do NOT touch today's CLOSE if already entered
        df_today = st.session_state.df_caisse_today.copy()
        denom_to_close_today = {r["Dénomination"]: r.get("CLOSE", 0) for _, r in df_today.iterrows()}
        df_today["OPEN"] = df_today["Dénomination"].map(lambda k: int(restant_y.get(k, 0)))
        df_today["CLOSE"] = df_today["Dénomination"].map(lambda k: int(safe_int(denom_to_close_today.get(k, 0), default=0) or 0))
        st.session_state.df_caisse_today = df_today

        st.divider()
        st.markdown("### ✅ Aujourd'hui — OPEN pré-rempli (RESTANT d'hier), entre le CLOSE d'aujourd'hui")

    else:
        st.caption("Ouverture normale: entre OPEN (matin) et CLOSE (fin de journée). L'app propose le retrait pour revenir à la cible.")

    # ===== Today table =====
    prev_today = st.session_state.df_caisse_today.copy()
    edited = st.data_editor(
        st.session_state.df_caisse_today,
        hide_index=True,
        use_container_width=True,
        height=520,
        column_config={
            "Dénomination": st.column_config.TextColumn(disabled=True),
            "OPEN": st.column_config.NumberColumn(min_value=0, step=1),
            "CLOSE": st.column_config.NumberColumn(min_value=0, step=1),
            "RETRAIT": st.column_config.NumberColumn(disabled=True),
            "RESTANT": st.column_config.NumberColumn(disabled=True),
        },
        key="ed_caisse_today",
    )

    # Gentle coerce OPEN/CLOSE only (preserve user input instead of zeroing)
    edited = gentle_coerce_editable(edited, prev_today, editable_cols=["OPEN", "CLOSE"])
    st.session_state.df_caisse_today = edited

    open_today = {r["Dénomination"]: int(r["OPEN"]) for _, r in st.session_state.df_caisse_today.iterrows()}
    close_today = {r["Dénomination"]: int(r["CLOSE"]) for _, r in st.session_state.df_caisse_today.iterrows()}

    total_open_today = total_cents(open_today)
    total_close_today = total_cents(close_today)
    diff_today = total_close_today - TARGET

    retrait_today = {k: 0 for k in DENOMS}
    restant_today = dict(close_today)
    remaining_today = 0

    if diff_today > 0:
        st.session_state.locked_retrait_caisse = clamp_locked(st.session_state.locked_retrait_caisse, close_today)
        retrait_today, remaining_today = suggest_withdrawal(
            diff_today,
            allowed=DISPLAY_ORDER,
            avail=close_today,
            locked=dict(st.session_state.locked_retrait_caisse),
            priority=PRIORITY_CAISSE,
        )
        restant_today = sub_counts(close_today, retrait_today)

    # Update computed cols only
    df_t = st.session_state.df_caisse_today.copy()
    df_t["RETRAIT"] = df_t["Dénomination"].map(lambda k: int(retrait_today.get(k, 0)))
    df_t["RESTANT"] = df_t["Dénomination"].map(lambda k: int(restant_today.get(k, 0)))
    st.session_state.df_caisse_today = df_t

    # Totals under columns (TODAY)
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        st.markdown(f"<div class='totals-label'>TOTAL OPEN</div><div class='totals-box'>{cents_to_str(total_open_today)}</div>", unsafe_allow_html=True)
    with f2:
        st.markdown(f"<div class='totals-label'>TOTAL CLOSE</div><div class='totals-box'>{cents_to_str(total_close_today)}</div>", unsafe_allow_html=True)
    with f3:
        st.markdown(f"<div class='totals-label'>À retirer</div><div class='totals-box'>{cents_to_str(diff_today)}</div>", unsafe_allow_html=True)
    with f4:
        st.markdown(f"<div class='totals-label'>TOTAL RESTANT</div><div class='totals-box'>{cents_to_str(total_cents(restant_today))}</div>", unsafe_allow_html=True)

    st.divider()
    st.subheader("Ajuster RETRAIT (aujourd'hui) (➖/➕)")

    if diff_today <= 0:
        st.warning("Sous la cible (ou égal). Aucun retrait nécessaire.")
    else:
        if remaining_today == 0:
            st.success("Retrait proposé: " + cents_to_str(total_cents(retrait_today)))
        elif remaining_today < 0:
            st.warning("Verrouillage trop haut. Dépasse de " + cents_to_str(-remaining_today))
        else:
            st.warning("Impossible exact. Reste: " + cents_to_str(remaining_today))

        r0, r1 = st.columns([1.3, 3.7])
        with r0:
            if st.button("Reset ajustements retrait (aujourd'hui)", key="btn_reset_today"):
                st.session_state.locked_retrait_caisse = {}
                st.rerun()
        with r1:
            st.caption("➖/➕ verrouille la dénomination et recalcule le reste.")

        for k in DISPLAY_ORDER:
            mx = int(close_today.get(k, 0))
            q = int(retrait_today.get(k, 0))
            c0, c1, c2, c3, c4 = st.columns([2.6, 0.7, 1.0, 0.7, 1.0], vertical_alignment="center")
            c0.markdown(f"**{k}**")
            minus = c1.button("➖", key=f"t_minus_{k}")
            c2.markdown(f"**{q}**")
            plus = c3.button("➕", key=f"t_plus_{k}")
            c4.caption(f"Dispo: {mx}")
            if minus or plus:
                new_locked = dict(st.session_state.locked_retrait_caisse)
                if k not in new_locked:
                    new_locked[k] = q
                if minus:
                    new_locked[k] = max(0, int(new_locked[k]) - 1)
                if plus:
                    new_locked[k] = min(mx, int(new_locked[k]) + 1)
                st.session_state.locked_retrait_caisse = new_locked
                st.rerun()

    # Receipt rows (today)
    rows_today = []
    for k in DISPLAY_ORDER:
        rows_today.append({
            "Dénomination": k,
            "OPEN": int(open_today.get(k, 0)),
            "CLOSE": int(close_today.get(k, 0)),
            "RETRAIT": int(retrait_today.get(k, 0)),
            "RESTANT": int(restant_today.get(k, 0)),
        })
    rows_today.append({
        "Dénomination": "TOTAL ($)",
        "OPEN": f"{total_open_today/100:.2f}",
        "CLOSE": f"{total_close_today/100:.2f}",
        "RETRAIT": f"{total_cents(retrait_today)/100:.2f}",
        "RESTANT": f"{total_cents(restant_today)/100:.2f}",
    })

    meta_caisse = {
        "Type": "CAISSE",
        "Date": today.isoformat(),
        "Généré à": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "Caisse #": int(st.session_state.register_no),
        "Caissier(ère)": (st.session_state.cashier.strip() or "—"),
        "Cible $": int(st.session_state.target_dollars),
        "Mode": "Ouverture non effectuée" if st.session_state.mode_pick == "missed_close" else "Ouverture normale",
    }

    payload_caisse = {
        "meta": meta_caisse,
        "register": {
            "table_today": st.session_state.df_caisse_today.to_dict("records"),
            "table_yesterday": st.session_state.df_caisse_yesterday.to_dict("records") if st.session_state.mode_pick == "missed_close" else None,
            "locked_retrait": st.session_state.locked_retrait_caisse,
            "rows_today": rows_today,
        },
    }

    state_path, receipt_path = caisse_paths(today)
    hc = hash_payload(payload_caisse)
    if st.session_state.last_hash_caisse != hc:
        html = receipt_html("Reçu — Caisse", meta_caisse, ["Dénomination", "OPEN", "CLOSE", "RETRAIT", "RESTANT"], rows_today)
        save_json(state_path, payload_caisse)
        save_receipt(receipt_path, html)
        st.session_state.last_hash_caisse = hc

    st.markdown("### Aperçu reçu — Caisse (aujourd'hui)")
    components.html(load_text(receipt_path) or html, height=640, scrolling=True)


# ================== TAB: BOÎTE ==================
with tab_boite:
    st.subheader("Boîte (Échange)")

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

    prev_b = st.session_state.df_boite.copy()
    edited_b = st.data_editor(
        st.session_state.df_boite,
        hide_index=True,
        use_container_width=True,
        height=520,
        column_config={
            "Dénomination": st.column_config.TextColumn(disabled=True),
            "Boîte (avant)": st.column_config.NumberColumn(min_value=0, step=1),
            "Dépôt": st.column_config.NumberColumn(min_value=0, step=1),
            "Change retiré": st.column_config.NumberColumn(disabled=True),
            "Boîte (après)": st.column_config.NumberColumn(disabled=True),
        },
        key="ed_boite",
    )

    # Gentle coerce ONLY editable cols
    edited_b = gentle_coerce_editable(edited_b, prev_b, editable_cols=["Boîte (avant)", "Dépôt"])
    st.session_state.df_boite = edited_b

    box_before = {r["Dénomination"]: int(r["Boîte (avant)"]) for _, r in st.session_state.df_boite.iterrows()}
    deposit = {r["Dénomination"]: int(r["Dépôt"]) for _, r in st.session_state.df_boite.iterrows()}

    total_before = total_cents(box_before)
    total_deposit = total_cents(deposit)

    box_after_deposit = add_counts(box_before, deposit)

    PRIORITY_BOITE = (
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
            priority=PRIORITY_BOITE,
        )
        box_after = sub_counts(box_after_deposit, withdraw)

    dfb = st.session_state.df_boite.copy()
    dfb["Change retiré"] = dfb["Dénomination"].map(lambda k: int(withdraw.get(k, 0)))
    dfb["Boîte (après)"] = dfb["Dénomination"].map(lambda k: int(box_after.get(k, 0)))
    st.session_state.df_boite = dfb

    # Totals under each “column meaning”
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        st.markdown(f"<div class='totals-label'>TOTAL BOÎTE (avant)</div><div class='totals-box'>{cents_to_str(total_before)}</div>", unsafe_allow_html=True)
    with f2:
        st.markdown(f"<div class='totals-label'>TOTAL DÉPÔT</div><div class='totals-box'>{cents_to_str(total_deposit)}</div>", unsafe_allow_html=True)
    with f3:
        st.markdown(f"<div class='totals-label'>CHANGE À RETIRER</div><div class='totals-box'>{cents_to_str(total_deposit)}</div>", unsafe_allow_html=True)
    with f4:
        st.markdown(f"<div class='totals-label'>TOTAL BOÎTE (après)</div><div class='totals-box'>{cents_to_str(total_cents(box_after))}</div>", unsafe_allow_html=True)

    st.divider()
    st.subheader("Ajuster Change retiré (➖/➕)")

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

        if st.button("Reset ajustements (boîte)", key="btn_reset_boite"):
            st.session_state.locked_withdraw_boite = {}
            st.rerun()

        allowed_sorted = [k for k in DISPLAY_ORDER if k in st.session_state.boite_allowed]
        for k in allowed_sorted:
            mx = int(box_after_deposit.get(k, 0))
            q = int(withdraw.get(k, 0))
            c0, c1, c2, c3, c4 = st.columns([2.6, 0.7, 1.0, 0.7, 1.0], vertical_alignment="center")
            c0.markdown(f"**{k}**")
            minus = c1.button("➖", key=f"b_minus_{k}")
            c2.markdown(f"**{q}**")
            plus = c3.button("➕", key=f"b_plus_{k}")
            c4.caption(f"Dispo: {mx}")
            if minus or plus:
                new_locked = dict(st.session_state.locked_withdraw_boite)
                if k not in new_locked:
                    new_locked[k] = q
                if minus:
                    new_locked[k] = max(0, int(new_locked[k]) - 1)
                if plus:
                    new_locked[k] = min(mx, int(new_locked[k]) + 1)
                st.session_state.locked_withdraw_boite = new_locked
                st.rerun()

    # Receipt (boîte)
    rows_boite = []
    for k in DISPLAY_ORDER:
        rows_boite.append({
            "Dénomination": k,
            "Boîte (avant)": int(box_before.get(k, 0)),
            "Dépôt": int(deposit.get(k, 0)),
            "Change retiré": int(withdraw.get(k, 0)),
            "Boîte (après)": int(box_after.get(k, 0)),
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

    state_path_b, receipt_path_b = boite_paths(today)
    hb = hash_payload(payload_boite)
    if st.session_state.last_hash_boite != hb:
        htmlb = receipt_html("Reçu — Boîte (Échange)", meta_boite, ["Dénomination","Boîte (avant)","Dépôt","Change retiré","Boîte (après)"], rows_boite)
        save_json(state_path_b, payload_boite)
        save_receipt(receipt_path_b, htmlb)
        st.session_state.last_hash_boite = hb

    st.markdown("### Aperçu reçu — Boîte (Échange)")
    components.html(load_text(receipt_path_b) or htmlb, height=640, scrolling=True)


# ================== TAB: SAUVEGARDE ==================
with tab_save:
    st.subheader("Sauvegarde & reçus")
    st.caption("Clique une date pour voir le reçu détaillé et télécharger les fichiers.")

    colA, colB = st.columns(2)

    with colA:
        st.markdown("## 📒 Caisse")
        dates = list_saved_dates(DIR_CAISSE)
        if not dates:
            st.info("Aucun enregistrement Caisse.")
        else:
            for ds in reversed(dates):
                d = date.fromisoformat(ds)
                state_path, receipt_path = caisse_paths(d)
                with st.expander(f"{ds} — Reçu Caisse", expanded=False):
                    html = load_text(receipt_path)
                    if html:
                        components.html(html, height=650, scrolling=True)
                    else:
                        st.warning("Reçu introuvable.")
                    if os.path.exists(receipt_path):
                        with open(receipt_path, "rb") as f:
                            st.download_button("⬇️ Télécharger reçu (HTML)", f.read(), os.path.basename(receipt_path), "text/html", key=f"dl_c_html_{ds}")
                    if os.path.exists(state_path):
                        with open(state_path, "rb") as f:
                            st.download_button("⬇️ Télécharger état (JSON)", f.read(), os.path.basename(state_path), "application/json", key=f"dl_c_json_{ds}")

    with colB:
        st.markdown("## 🪙 Boîte (Échange)")
        dates = list_saved_dates(DIR_BOITE)
        if not dates:
            st.info("Aucun enregistrement Boîte.")
        else:
            for ds in reversed(dates):
                d = date.fromisoformat(ds)
                state_path, receipt_path = boite_paths(d)
                with st.expander(f"{ds} — Reçu Boîte (Échange)", expanded=False):
                    html = load_text(receipt_path)
                    if html:
                        components.html(html, height=650, scrolling=True)
                    else:
                        st.warning("Reçu introuvable.")
                    if os.path.exists(receipt_path):
                        with open(receipt_path, "rb") as f:
                            st.download_button("⬇️ Télécharger reçu (HTML)", f.read(), os.path.basename(receipt_path), "text/html", key=f"dl_b_html_{ds}")
                    if os.path.exists(state_path):
                        with open(state_path, "rb") as f:
                            st.download_button("⬇️ Télécharger état (JSON)", f.read(), os.path.basename(state_path), "application/json", key=f"dl_b_json_{ds}")
