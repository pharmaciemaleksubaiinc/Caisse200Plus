# caisse200+.py
# Registre — Caisse & Boîte (Échange)
# Version: NO st.data_editor for entry (fixes "Enter resets to 0")
# - Auth
# - After login: choose mode (Ouverture normale / Ouverture non effectuée)
# - Caisse: grid OPEN/CLOSE/RETRAIT/RESTANT on same line, with +/- for RETRAIT
# - Totals under each column
# - Missed close mode: enter CLOSE hier -> compute RETRAIT hier -> OPEN today = RESTANT hier
# - Boîte: enter Box Before + Deposit, choose allowed change, compute Change Retiré with +/- locks
# - Sauvegarde tab: list by date -> expandable receipt + downloads (Caisse & Boîte separate)

import os
import json
import hashlib
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

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
.main .block-container { padding-top: 0.8rem !important; padding-bottom: 0.8rem !important; max-width: 1600px !important; }
h1,h2,h3 { margin-bottom: 0.35rem !important; }

.grid-head { font-weight: 900; font-size: 14px; opacity: .85; padding: 6px 0; }
.grid-denom { font-weight: 900; font-size: 14px; }
.grid-num { font-weight: 900; font-size: 14px; text-align:center; }

.totals-label { font-weight: 900; font-size: 14px; opacity: 0.75; margin-bottom: 6px; }
.totals-box { font-weight: 950; font-size: 18px; padding: 12px; border-radius: 14px; background: rgba(0,0,0,0.04); text-align:center; }

.smallcap { opacity: .70; font-size: 12px; }

button[kind="secondary"] { font-weight: 800 !important; }
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
def cents_to_str(c: int) -> str:
    return f"{c/100:.2f} $"

def total_cents(counts: dict) -> int:
    return sum(int(counts.get(k, 0)) * DENOMS[k] for k in DENOMS)

def sub_counts(a: dict, b: dict) -> dict:
    return {k: int(a.get(k, 0)) - int(b.get(k, 0)) for k in DENOMS}

def add_counts(a: dict, b: dict) -> dict:
    return {k: int(a.get(k, 0)) + int(b.get(k, 0)) for k in DENOMS}

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

def take_greedy(remaining: int, keys_order: list, avail: dict, out: dict, locked: dict) -> int:
    for k in keys_order:
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

def suggest(amount_cents: int, allowed: list, avail: dict, locked: dict, priority: list):
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

def save_text(path: str, txt: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)

def load_text(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def list_dates(folder: str):
    files = sorted([f for f in os.listdir(folder) if f.endswith("_state.json")])
    return [f.replace("_state.json", "") for f in files]

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


# ================== UI GRID WIDGETS ==================
def ensure_counts(prefix: str, keys: list):
    if prefix not in st.session_state:
        st.session_state[prefix] = {k: 0 for k in keys}

def get_counts(prefix: str) -> dict:
    return dict(st.session_state.get(prefix, {}))

def set_count(prefix: str, k: str, v: int):
    st.session_state[prefix][k] = int(v)

def grid_header(labels):
    cols = st.columns([2.8, 1.2, 1.2, 1.6, 1.6])
    for c, lab in zip(cols, labels):
        c.markdown(f"<div class='grid-head'>{lab}</div>", unsafe_allow_html=True)
    return cols

def caisse_grid(open_prefix, close_prefix, retrait_counts: dict, restant_counts: dict,
               allow_edit_open=True, allow_edit_close=True):
    ensure_counts(open_prefix, DISPLAY_ORDER)
    ensure_counts(close_prefix, DISPLAY_ORDER)

    grid_header(["Dénomination", "OPEN", "CLOSE", "RETRAIT", "RESTANT"])

    for k in DISPLAY_ORDER:
        row = st.columns([2.8, 1.2, 1.2, 1.6, 1.6], vertical_alignment="center")
        row[0].markdown(f"<div class='grid-denom'>{k}</div>", unsafe_allow_html=True)

        # OPEN input
        open_val = st.session_state[open_prefix].get(k, 0)
        if allow_edit_open:
            v = row[1].number_input("", min_value=0, step=1, value=int(open_val), key=f"{open_prefix}_{k}")
            set_count(open_prefix, k, v)
        else:
            row[1].markdown(f"<div class='grid-num'>{int(open_val)}</div>", unsafe_allow_html=True)

        # CLOSE input
        close_val = st.session_state[close_prefix].get(k, 0)
        if allow_edit_close:
            v = row[2].number_input("", min_value=0, step=1, value=int(close_val), key=f"{close_prefix}_{k}")
            set_count(close_prefix, k, v)
        else:
            row[2].markdown(f"<div class='grid-num'>{int(close_val)}</div>", unsafe_allow_html=True)

        row[3].markdown(f"<div class='grid-num'>{int(retrait_counts.get(k, 0))}</div>", unsafe_allow_html=True)
        row[4].markdown(f"<div class='grid-num'>{int(restant_counts.get(k, 0))}</div>", unsafe_allow_html=True)

def boite_grid(before_prefix, deposit_prefix, withdraw_counts: dict, after_counts: dict,
              allow_edit_before=True, allow_edit_deposit=True):
    ensure_counts(before_prefix, DISPLAY_ORDER)
    ensure_counts(deposit_prefix, DISPLAY_ORDER)

    grid_header(["Dénomination", "Boîte (avant)", "Dépôt", "Change retiré", "Boîte (après)"])

    for k in DISPLAY_ORDER:
        row = st.columns([2.8, 1.2, 1.2, 1.6, 1.6], vertical_alignment="center")
        row[0].markdown(f"<div class='grid-denom'>{k}</div>", unsafe_allow_html=True)

        before_val = st.session_state[before_prefix].get(k, 0)
        if allow_edit_before:
            v = row[1].number_input("", min_value=0, step=1, value=int(before_val), key=f"{before_prefix}_{k}")
            set_count(before_prefix, k, v)
        else:
            row[1].markdown(f"<div class='grid-num'>{int(before_val)}</div>", unsafe_allow_html=True)

        dep_val = st.session_state[deposit_prefix].get(k, 0)
        if allow_edit_deposit:
            v = row[2].number_input("", min_value=0, step=1, value=int(dep_val), key=f"{deposit_prefix}_{k}")
            set_count(deposit_prefix, k, v)
        else:
            row[2].markdown(f"<div class='grid-num'>{int(dep_val)}</div>", unsafe_allow_html=True)

        row[3].markdown(f"<div class='grid-num'>{int(withdraw_counts.get(k, 0))}</div>", unsafe_allow_html=True)
        row[4].markdown(f"<div class='grid-num'>{int(after_counts.get(k, 0))}</div>", unsafe_allow_html=True)


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


# ================== GLOBAL STATE ==================
today = datetime.now(TZ).date()
yesterday = today - timedelta(days=1)

if "mode_pick" not in st.session_state:
    st.session_state.mode_pick = None
if "mode_done_today" not in st.session_state:
    st.session_state.mode_done_today = None

if st.session_state.mode_done_today != today.isoformat():
    st.session_state.mode_pick = None

# App meta state
if "cashier" not in st.session_state:
    st.session_state.cashier = ""
if "register_no" not in st.session_state:
    st.session_state.register_no = 1
if "target_dollars" not in st.session_state:
    st.session_state.target_dollars = 200

# Locks
if "locked_retrait_caisse" not in st.session_state:
    st.session_state.locked_retrait_caisse = {}
if "locked_retrait_hier" not in st.session_state:
    st.session_state.locked_retrait_hier = {}

if "boite_allowed" not in st.session_state:
    st.session_state.boite_allowed = set(["Billet 20 $", "Billet 10 $", "Billet 5 $"] + COINS + ROLLS)
if "locked_withdraw_boite" not in st.session_state:
    st.session_state.locked_withdraw_boite = {}

# autosave hashes
if "last_hash_caisse" not in st.session_state:
    st.session_state.last_hash_caisse = None
if "last_hash_boite" not in st.session_state:
    st.session_state.last_hash_boite = None

# Load saved state once per day (optional, keeps continuity)
if "booted_for" not in st.session_state:
    st.session_state.booted_for = None

if st.session_state.booted_for != today.isoformat():
    st.session_state.booted_for = today.isoformat()

    # Caisse saved
    state_path_c, _ = caisse_paths(today)
    saved = load_json(state_path_c)
    if saved:
        meta = saved.get("meta", {})
        st.session_state.cashier = meta.get("Caissier(ère)", st.session_state.cashier)
        st.session_state.register_no = int(meta.get("Caisse #", st.session_state.register_no))
        st.session_state.target_dollars = int(meta.get("Cible $", st.session_state.target_dollars))
        st.session_state.mode_pick = saved.get("mode_pick", st.session_state.mode_pick)

        # restore counts dicts if present
        counts = saved.get("counts", {})
        for name, dct in counts.items():
            if isinstance(dct, dict):
                st.session_state[name] = dct

        st.session_state.locked_retrait_caisse = saved.get("locked_retrait_caisse", {}) or {}
        st.session_state.locked_retrait_hier = saved.get("locked_retrait_hier", {}) or {}

    # Boîte saved
    state_path_b, _ = boite_paths(today)
    savedb = load_json(state_path_b)
    if savedb:
        meta = savedb.get("meta", {})
        st.session_state.boite_allowed = set(savedb.get("boite_allowed", list(st.session_state.boite_allowed)))
        counts = savedb.get("counts", {})
        for name, dct in counts.items():
            if isinstance(dct, dict):
                st.session_state[name] = dct
        st.session_state.locked_withdraw_boite = savedb.get("locked_withdraw_boite", {}) or {}


# ================== MODE PICKER ==================
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


# ================== HEADER ==================
st.title("Registre — Caisse & Boîte de monnaie")

h1, h2, h3, h4 = st.columns([1.1, 1.0, 1.2, 2.0])
with h1:
    st.write("**Date:**", today.isoformat())
with h2:
    st.write("**Heure:**", datetime.now(TZ).strftime("%H:%M"))
with h3:
    st.session_state.register_no = st.selectbox("Caisse #", [1, 2, 3], index=[1,2,3].index(int(st.session_state.register_no)), key="reg_sel")
with h4:
    st.session_state.cashier = st.text_input("Caissier(ère)", value=st.session_state.cashier, key="cashier_txt")

st.session_state.target_dollars = st.number_input("Cible à laisser ($)", min_value=0, step=10, value=int(st.session_state.target_dollars), key="target_num")

st.divider()
tab_caisse, tab_boite, tab_save = st.tabs(["1) Caisse", "2) Boîte (Échange)", "3) Sauvegarde & reçus"])


# ================== TAB: CAISSE ==================
with tab_caisse:
    TARGET = int(st.session_state.target_dollars) * 100

    coins_desc = sorted(COINS, key=lambda x: DENOMS[x], reverse=True)
    rolls_desc = sorted(ROLLS, key=lambda x: DENOMS[x], reverse=True)
    PRIORITY_CAISSE = BILLS_BIG + BILLS_SMALL + coins_desc + rolls_desc

    st.subheader("Caisse")

    # Prefixes for today
    OPEN_T = "caisse_open_today"
    CLOSE_T = "caisse_close_today"

    # Prefixes for yesterday (only in missed_close mode)
    CLOSE_Y = "caisse_close_yesterday"

    # Ensure base dicts exist (so number_input never resets)
    ensure_counts(OPEN_T, DISPLAY_ORDER)
    ensure_counts(CLOSE_T, DISPLAY_ORDER)
    ensure_counts(CLOSE_Y, DISPLAY_ORDER)

    # ================== MISSED CLOSE MODE ==================
    close_y = {k: 0 for k in DISPLAY_ORDER}
    retrait_y = {k: 0 for k in DISPLAY_ORDER}
    restant_y = {k: 0 for k in DISPLAY_ORDER}

    if st.session_state.mode_pick == "missed_close":
        st.markdown("### ⚠️ Hier — fermeture non effectuée")
        st.caption("Entre le CLOSE d'hier. L'app calcule le retrait et prépare OPEN d'aujourd'hui (restant).")

        # CLOSE hier grid (only close editable)
        # We'll display it in the same table style by reusing caisse_grid but locking OPEN
        dummy_open_y = {k: 0 for k in DISPLAY_ORDER}
        close_y = get_counts(CLOSE_Y)

        # compute retrait hier from CLOSE hier
        total_close_y = total_cents(close_y)
        diff_y = total_close_y - TARGET

        if diff_y > 0:
            st.session_state.locked_retrait_hier = clamp_locked(st.session_state.locked_retrait_hier, close_y)
            retrait_y_full, remaining_y = suggest(
                diff_y,
                allowed=DISPLAY_ORDER,
                avail=close_y,
                locked=dict(st.session_state.locked_retrait_hier),
                priority=PRIORITY_CAISSE,
            )
            retrait_y = dict(retrait_y_full)
            restant_y = sub_counts(close_y, retrait_y)
        else:
            remaining_y = 0
            retrait_y = {k: 0 for k in DISPLAY_ORDER}
            restant_y = dict(close_y)

        # Render yesterday "table": OPEN locked at 0, CLOSE editable, computed retrait/restant
        caisse_grid(
            open_prefix="__dummy_open_y",  # will be created but not used
            close_prefix=CLOSE_Y,
            retrait_counts=retrait_y,
            restant_counts=restant_y,
            allow_edit_open=False,
            allow_edit_close=True,
        )

        # totals under columns (yesterday)
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

            if st.button("Reset ajustements retrait (hier)", key="reset_lock_y"):
                st.session_state.locked_retrait_hier = {}
                st.rerun()

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
                    new_locked = dict(st.session_state.locked_retrait_hier)
                    if k not in new_locked:
                        new_locked[k] = q
                    if minus:
                        new_locked[k] = max(0, int(new_locked[k]) - 1)
                    if plus:
                        new_locked[k] = min(mx, int(new_locked[k]) + 1)
                    st.session_state.locked_retrait_hier = new_locked
                    st.rerun()

        # populate OPEN today from restant_y (but do not touch CLOSE today)
        open_today_dict = get_counts(OPEN_T)
        for k in DISPLAY_ORDER:
            open_today_dict[k] = int(restant_y.get(k, 0))
        st.session_state[OPEN_T] = open_today_dict

        st.divider()
        st.markdown("### ✅ Aujourd'hui — OPEN pré-rempli (RESTANT d'hier), entre le CLOSE d'aujourd'hui")

    else:
        st.caption("Ouverture normale: entre OPEN (matin) et CLOSE (fin de journée). Retrait proposé automatiquement.")

    # ================== TODAY COMPUTE ==================
    open_today = get_counts(OPEN_T)
    close_today = get_counts(CLOSE_T)

    total_open_today = total_cents(open_today)
    total_close_today = total_cents(close_today)
    diff_today = total_close_today - TARGET

    retrait_today = {k: 0 for k in DISPLAY_ORDER}
    restant_today = dict(close_today)
    remaining_today = 0

    if diff_today > 0:
        st.session_state.locked_retrait_caisse = clamp_locked(st.session_state.locked_retrait_caisse, close_today)
        retrait_today_full, remaining_today = suggest(
            diff_today,
            allowed=DISPLAY_ORDER,
            avail=close_today,
            locked=dict(st.session_state.locked_retrait_caisse),
            priority=PRIORITY_CAISSE,
        )
        retrait_today = dict(retrait_today_full)
        restant_today = sub_counts(close_today, retrait_today)

    # ================== TODAY GRID ==================
    caisse_grid(
        open_prefix=OPEN_T,
        close_prefix=CLOSE_T,
        retrait_counts=retrait_today,
        restant_counts=restant_today,
        allow_edit_open=True if st.session_state.mode_pick == "normal" else False,  # in missed mode, OPEN is auto
        allow_edit_close=True,
    )

    # Totals under each column (today)
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
        st.warning("Sous la cible (ou égal). Aucun retrait.")
    else:
        if remaining_today == 0:
            st.success("Retrait proposé: " + cents_to_str(total_cents(retrait_today)))
        elif remaining_today < 0:
            st.warning("Verrouillage trop haut. Dépasse de " + cents_to_str(-remaining_today))
        else:
            st.warning("Impossible exact. Reste: " + cents_to_str(remaining_today))

        if st.button("Reset ajustements retrait (aujourd'hui)", key="reset_lock_t"):
            st.session_state.locked_retrait_caisse = {}
            st.rerun()

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
        "mode_pick": st.session_state.mode_pick,
        "counts": {
            OPEN_T: st.session_state[OPEN_T],
            CLOSE_T: st.session_state[CLOSE_T],
            CLOSE_Y: st.session_state[CLOSE_Y] if st.session_state.mode_pick == "missed_close" else {k: 0 for k in DISPLAY_ORDER},
        },
        "locked_retrait_caisse": st.session_state.locked_retrait_caisse,
        "locked_retrait_hier": st.session_state.locked_retrait_hier,
        "rows_today": rows_today,
    }

    state_path, receipt_path = caisse_paths(today)
    hc = hash_payload(payload_caisse)
    if st.session_state.last_hash_caisse != hc:
        html = receipt_html("Reçu — Caisse", meta_caisse, ["Dénomination", "OPEN", "CLOSE", "RETRAIT", "RESTANT"], rows_today)
        save_json(state_path, payload_caisse)
        save_text(receipt_path, html)
        st.session_state.last_hash_caisse = hc

    st.markdown("### Aperçu reçu — Caisse")
    components.html(load_text(receipt_path) or html, height=640, scrolling=True)


# ================== TAB: BOÎTE ==================
with tab_boite:
    st.subheader("Boîte (Échange)")
    st.caption("Entre le contenu de la boîte et ce que tu y déposes depuis la caisse. L'app propose le change à retirer (favorise petites coupures/pièces/rouleaux).")

    # Allowed denom selection
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

    BEFORE_B = "boite_before"
    DEPOSIT_B = "boite_deposit"
    ensure_counts(BEFORE_B, DISPLAY_ORDER)
    ensure_counts(DEPOSIT_B, DISPLAY_ORDER)

    box_before = get_counts(BEFORE_B)
    deposit = get_counts(DEPOSIT_B)

    total_before = total_cents(box_before)
    total_deposit = total_cents(deposit)

    box_after_deposit = add_counts(box_before, deposit)

    PRIORITY_BOITE = (
        ["Billet 20 $", "Billet 10 $", "Billet 5 $"]
        + ["Pièce 2 $", "Pièce 1 $", "Pièce 0,25 $", "Pièce 0,10 $", "Pièce 0,05 $"]
        + ROLLS
        + ["Billet 50 $", "Billet 100 $"]
    )

    withdraw = {k: 0 for k in DISPLAY_ORDER}
    box_after = dict(box_after_deposit)
    remaining = 0

    if total_deposit > 0 and st.session_state.boite_allowed:
        st.session_state.locked_withdraw_boite = clamp_locked(st.session_state.locked_withdraw_boite, box_after_deposit)
        withdraw_full, remaining = suggest(
            total_deposit,
            allowed=list(st.session_state.boite_allowed),
            avail=box_after_deposit,
            locked=dict(st.session_state.locked_withdraw_boite),
            priority=PRIORITY_BOITE,
        )
        withdraw = dict(withdraw_full)
        box_after = sub_counts(box_after_deposit, withdraw)

    # Grid
    boite_grid(
        before_prefix=BEFORE_B,
        deposit_prefix=DEPOSIT_B,
        withdraw_counts=withdraw,
        after_counts=box_after,
        allow_edit_before=True,
        allow_edit_deposit=True,
    )

    # Totals under each column meaning
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

        if st.button("Reset ajustements (boîte)", key="reset_lock_boite"):
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
        "boite_allowed": sorted(list(st.session_state.boite_allowed)),
        "counts": {
            BEFORE_B: st.session_state[BEFORE_B],
            DEPOSIT_B: st.session_state[DEPOSIT_B],
        },
        "locked_withdraw_boite": st.session_state.locked_withdraw_boite,
        "rows": rows_boite,
    }

    state_path_b, receipt_path_b = boite_paths(today)
    hb = hash_payload(payload_boite)
    if st.session_state.last_hash_boite != hb:
        htmlb = receipt_html("Reçu — Boîte (Échange)", meta_boite, ["Dénomination","Boîte (avant)","Dépôt","Change retiré","Boîte (après)"], rows_boite)
        save_json(state_path_b, payload_boite)
        save_text(receipt_path_b, htmlb)
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
        dates = list_dates(DIR_CAISSE)
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
        dates = list_dates(DIR_BOITE)
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
