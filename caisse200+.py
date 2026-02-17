# registre_200_app.py
# Caisse + Boîte de monnaie (Change Box)
# - 2 onglets
# - Caisse: retrait favorise 100/50/20, puis 10/5 rarement, pièces pour finir, rouleaux dernier
# - Change box: transferts/retraits favorisent pièces + rouleaux + 10/5 (petit), gros billets en dernier
# - Persistant par date: charge automatiquement l'état du jour, sauvegarde auto + export Excel quotidien
# - Téléchargement du reçu (Excel) + état (JSON)
# - Timezone Montréal (America/Toronto)
# - Auth via st.secrets["APP_PASSWORD"] (avec fallback propre)

import os
import json
import hashlib
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# ================== CONFIG APP ==================
st.set_page_config(page_title="Caisse & Boîte de monnaie — Registre quotidien", layout="wide")
TZ = ZoneInfo("America/Toronto")

DATA_DIR = "data"
RECORDS_DIR = os.path.join(DATA_DIR, "records")
os.makedirs(RECORDS_DIR, exist_ok=True)

# ================== AUTH ==================
if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.title("Accès protégé")
    pwd = st.text_input("Mot de passe", type="password")
    app_password = st.secrets.get("APP_PASSWORD", None)
    if st.button("Se connecter"):
        if app_password is None:
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
    # Billets
    "Billet 100 $": 10000,
    "Billet 50 $": 5000,
    "Billet 20 $": 2000,
    "Billet 10 $": 1000,
    "Billet 5 $": 500,
    # Pièces
    "Pièce 2 $": 200,
    "Pièce 1 $": 100,
    "Pièce 0,25 $": 25,
    "Pièce 0,10 $": 10,
    "Pièce 0,05 $": 5,
    # Rouleaux
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
    return f"{c / 100:.2f} $"

def total_cents(counts: dict) -> int:
    return sum(int(counts.get(k, 0)) * DENOMS[k] for k in DENOMS)

def sub_counts(a: dict, b: dict) -> dict:
    return {k: int(a.get(k, 0)) - int(b.get(k, 0)) for k in DENOMS}

def add_counts(a: dict, b: dict) -> dict:
    return {k: int(a.get(k, 0)) + int(b.get(k, 0)) for k in DENOMS}

def clamp_counts(counts: dict) -> dict:
    out = {}
    for k in DENOMS:
        v = int(counts.get(k, 0))
        if v < 0:
            v = 0
        out[k] = v
    return out

def clamp_locked(locked: dict, avail: dict) -> dict:
    out = {}
    for k, v in locked.items():
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

def suggest_by_priority(amount_cents: int, allowed: list, avail: dict, locked: dict, priority_keys: list):
    """
    Greedy par priorité explicitement donnée.
    - amount_cents: montant à couvrir
    - allowed: types autorisés
    - avail: dispo
    - locked: quantités verrouillées (déjà imposées)
    """
    out = {k: 0 for k in DENOMS}
    for k, q in locked.items():
        out[k] = int(q)

    remaining = amount_cents - total_cents(out)
    if remaining < 0:
        return out, remaining

    allowed_set = set(allowed)
    keys = [k for k in priority_keys if k in allowed_set]

    remaining = take_greedy(remaining, keys, avail, out, locked)
    return out, remaining

def suggest_retrait_caisse(diff_cents: int, allowed: list, avail: dict, locked: dict):
    # Caisse: gros billets d'abord, puis petits billets rarement, pièces pour finir, rouleaux dernier
    coins_desc = sorted([k for k in COINS], key=lambda x: DENOMS[x], reverse=True)
    rolls_desc = sorted([k for k in ROLLS], key=lambda x: DENOMS[x], reverse=True)
    priority = BILLS_BIG + BILLS_SMALL + coins_desc + rolls_desc
    return suggest_by_priority(diff_cents, allowed, avail, locked, priority)

def suggest_changebox(amount_cents: int, allowed: list, avail: dict, locked: dict):
    # Boîte monnaie: favoriser pièces + rouleaux + petits billets, gros billets en dernier
    coins_desc = sorted([k for k in COINS], key=lambda x: DENOMS[x], reverse=True)
    rolls_desc = sorted([k for k in ROLLS], key=lambda x: DENOMS[x], reverse=True)
    priority = coins_desc + rolls_desc + BILLS_SMALL + BILLS_BIG
    return suggest_by_priority(amount_cents, allowed, avail, locked, priority)

def dict_from_df(df: pd.DataFrame, col_qty: str) -> dict:
    return {row["Dénomination"]: int(row[col_qty]) for _, row in df.iterrows()}

def default_register_df():
    return pd.DataFrame([{"Dénomination": k, "OPEN": 0, "CLOSE": 0, "Autorisé retrait": True} for k in DISPLAY_ORDER])

def default_changebox_df():
    return pd.DataFrame([{"Dénomination": k, "Boîte (actuel)": 0, "Autorisé boîte": True} for k in DISPLAY_ORDER])

def safe_date_str(d: date) -> str:
    return d.isoformat()

def state_path_for(d: date) -> str:
    return os.path.join(RECORDS_DIR, f"{safe_date_str(d)}_state.json")

def excel_path_for(d: date) -> str:
    return os.path.join(RECORDS_DIR, f"{safe_date_str(d)}_registre.xlsx")

def hash_state(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def load_state_for(d: date):
    p = state_path_for(d)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state_for(d: date, payload: dict):
    p = state_path_for(d)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def export_excel_for(d: date, payload: dict):
    """
    Écrit un fichier Excel journalier avec:
    - Meta
    - Caisse (inputs + calcul)
    - Boîte de monnaie (inputs + calcul)
    """
    xlsx = excel_path_for(d)

    meta = payload.get("meta", {})
    reg = payload.get("register", {})
    box = payload.get("changebox", {})

    meta_rows = [{"Champ": k, "Valeur": str(v)} for k, v in meta.items()]
    df_meta = pd.DataFrame(meta_rows)

    df_reg_inputs = pd.DataFrame(reg.get("table", []))
    df_reg_calc = pd.DataFrame(reg.get("calc_rows", []))

    df_box_inputs = pd.DataFrame(box.get("table", []))
    df_box_calc = pd.DataFrame(box.get("calc_rows", []))

    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        df_meta.to_excel(writer, sheet_name="Meta", index=False)
        df_reg_inputs.to_excel(writer, sheet_name="Caisse_Inputs", index=False)
        df_reg_calc.to_excel(writer, sheet_name="Caisse_Calcul", index=False)
        df_box_inputs.to_excel(writer, sheet_name="Boite_Inputs", index=False)
        df_box_calc.to_excel(writer, sheet_name="Boite_Calcul", index=False)

    return xlsx

def rows_calc_table(open_c, close_c, retrait_c, restant_c):
    rows = []
    for k in DISPLAY_ORDER:
        rows.append({
            "Dénomination": k,
            "OPEN": int(open_c.get(k, 0)),
            "CLOSE": int(close_c.get(k, 0)),
            "RETRAIT": int(retrait_c.get(k, 0)),
            "RESTANT": int(restant_c.get(k, 0)),
        })
    rows.append({
        "Dénomination": "TOTAL ($)",
        "OPEN": f"{total_cents(open_c)/100:.2f}",
        "CLOSE": f"{total_cents(close_c)/100:.2f}",
        "RETRAIT": f"{total_cents(retrait_c)/100:.2f}",
        "RESTANT": f"{total_cents(restant_c)/100:.2f}",
    })
    return rows

def rows_box_calc_table(box_now, to_box, from_box, box_after):
    rows = []
    for k in DISPLAY_ORDER:
        rows.append({
            "Dénomination": k,
            "Boîte (actuel)": int(box_now.get(k, 0)),
            "Vers boîte": int(to_box.get(k, 0)),
            "Depuis boîte": int(from_box.get(k, 0)),
            "Boîte (après)": int(box_after.get(k, 0)),
        })
    rows.append({
        "Dénomination": "TOTAL ($)",
        "Boîte (actuel)": f"{total_cents(box_now)/100:.2f}",
        "Vers boîte": f"{total_cents(to_box)/100:.2f}",
        "Depuis boîte": f"{total_cents(from_box)/100:.2f}",
        "Boîte (après)": f"{total_cents(box_after)/100:.2f}",
    })
    return rows

# ================== DAILY STATE BOOT ==================
today = datetime.now(TZ).date()

if "active_date" not in st.session_state:
    st.session_state.active_date = today

# If day changed (or first run), load today's state if exists, else init fresh
if "booted" not in st.session_state or st.session_state.active_date != today:
    st.session_state.active_date = today
    st.session_state.booted = True

    existing = load_state_for(today)
    if existing:
        st.session_state.meta_cashier = existing.get("meta", {}).get("Caissier(ère)", "")
        st.session_state.meta_register_no = int(existing.get("meta", {}).get("Caisse #", 1))
        st.session_state.meta_target = int(existing.get("meta", {}).get("Cible $", 200))

        st.session_state.df_register = pd.DataFrame(existing.get("register", {}).get("table", default_register_df().to_dict("records")))
        st.session_state.df_changebox = pd.DataFrame(existing.get("changebox", {}).get("table", default_changebox_df().to_dict("records")))

        st.session_state.locked_retrait = existing.get("register", {}).get("locked_retrait", {})
        st.session_state.locked_to_box = existing.get("changebox", {}).get("locked_to_box", {})
        st.session_state.locked_from_box = existing.get("changebox", {}).get("locked_from_box", {})
        st.session_state.box_target = int(existing.get("changebox", {}).get("box_target", 0))
    else:
        st.session_state.meta_cashier = ""
        st.session_state.meta_register_no = 1
        st.session_state.meta_target = 200

        st.session_state.df_register = default_register_df()
        st.session_state.df_changebox = default_changebox_df()

        st.session_state.locked_retrait = {}
        st.session_state.locked_to_box = {}
        st.session_state.locked_from_box = {}
        st.session_state.box_target = 0

    st.session_state.last_saved_hash = None
    st.session_state.last_saved_at = None

# ================== TOP BAR ==================
st.title("Registre quotidien — Caisse & Boîte de monnaie")
st.caption("Ça sauvegarde par date. Ça garde les données. Comme un vrai système. Incroyable.")

top1, top2, top3, top4, top5 = st.columns([1.1, 1.1, 1.2, 1.2, 2.2])
with top1:
    st.write("**Date:**", st.session_state.active_date.isoformat())
with top2:
    now_time = datetime.now(TZ).strftime("%H:%M")
    st.write("**Heure:**", now_time)
with top3:
    st.session_state.meta_register_no = st.selectbox("Caisse #", [1, 2, 3], index=[1,2,3].index(st.session_state.meta_register_no))
with top4:
    st.session_state.meta_cashier = st.text_input("Caissier(ère)", value=st.session_state.meta_cashier)
with top5:
    st.session_state.meta_target = st.number_input("Cible à laisser ($)", min_value=0, step=10, value=int(st.session_state.meta_target))

st.divider()

# ================== AUTOSAVE (smart) ==================
def build_payload_for_save(reg_calc_rows, box_calc_rows, extra_meta):
    payload = {
        "meta": extra_meta,
        "register": {
            "table": st.session_state.df_register.to_dict("records"),
            "locked_retrait": st.session_state.locked_retrait,
            "calc_rows": reg_calc_rows,
        },
        "changebox": {
            "table": st.session_state.df_changebox.to_dict("records"),
            "box_target": int(st.session_state.box_target),
            "locked_to_box": st.session_state.locked_to_box,
            "locked_from_box": st.session_state.locked_from_box,
            "calc_rows": box_calc_rows,
        },
    }
    return payload

def autosave_if_needed(payload: dict):
    h = hash_state(payload)
    if st.session_state.last_saved_hash == h:
        return
    save_state_for(today, payload)
    export_excel_for(today, payload)
    st.session_state.last_saved_hash = h
    st.session_state.last_saved_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

# ================== TABS ==================
tab1, tab2 = st.tabs(["1) Caisse (retour à la cible)", "2) Boîte de monnaie (change box)"])

# ================== TAB 1: CAISSE ==================
with tab1:
    st.subheader("Caisse — OPEN/CLOSE + Autorisés")
    st.caption("Tout en tableau pour réduire le scroll.")

    edited_reg = st.data_editor(
        st.session_state.df_register,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Dénomination": st.column_config.TextColumn(disabled=True),
            "OPEN": st.column_config.NumberColumn(min_value=0, step=1),
            "CLOSE": st.column_config.NumberColumn(min_value=0, step=1),
            "Autorisé retrait": st.column_config.CheckboxColumn(),
        },
        height=520
    )
    st.session_state.df_register = edited_reg

    open_counts = dict_from_df(edited_reg, "OPEN")
    close_counts = dict_from_df(edited_reg, "CLOSE")
    allowed_retrait = [r["Dénomination"] for _, r in edited_reg.iterrows() if bool(r["Autorisé retrait"])]

    TARGET = int(st.session_state.meta_target) * 100
    total_close = total_cents(close_counts)
    total_open = total_cents(open_counts)

    a, b, c = st.columns(3)
    a.info("TOTAL OPEN : " + cents_to_str(total_open))
    b.success("TOTAL CLOSE : " + cents_to_str(total_close))
    diff = total_close - TARGET
    c.write("**À retirer (CLOSE - cible):** " + f"**{cents_to_str(diff)}**")

    st.divider()
    st.subheader("Proposition de retrait + Ajustements")

    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([1.3, 1.3, 1.6, 2.8])
    with ctrl1:
        if st.button("Proposer retrait (reset ajustements)"):
            st.session_state.locked_retrait = {}
            st.rerun()
    with ctrl2:
        if st.button("Réinitialiser ajustements"):
            st.session_state.locked_retrait = {}
            st.rerun()
    with ctrl3:
        st.write("Verrouillés:", len(st.session_state.locked_retrait))
    with ctrl4:
        st.caption("➖/➕ verrouille une dénomination, le reste se recalcule.")

    retrait_counts = {k: 0 for k in DENOMS}
    restant_counts = dict(close_counts)
    remaining = 0

    if diff <= 0:
        st.warning("Caisse sous la cible (ou égale). Ici il faudrait AJOUTER, pas retirer.")
    elif not allowed_retrait:
        st.error("Choisis au moins un type autorisé.")
    else:
        st.session_state.locked_retrait = clamp_locked(st.session_state.locked_retrait, close_counts)
        locked = dict(st.session_state.locked_retrait)

        retrait_counts, remaining = suggest_retrait_caisse(diff, allowed_retrait, close_counts, locked)
        retrait_total = total_cents(retrait_counts)

        if remaining == 0:
            st.success("RETRAIT proposé: " + cents_to_str(retrait_total))
        elif remaining < 0:
            st.warning("Tu as dépassé de " + cents_to_str(-remaining) + " (verrouillage trop haut).")
        else:
            st.warning("Impossible exact. Reste non couvert: " + cents_to_str(remaining))

        # Horizontal grid for +/- adjustments
        adjust_keys = [k for k in DISPLAY_ORDER if k in allowed_retrait]
        cols_per_row = 4

        for i in range(0, len(adjust_keys), cols_per_row):
            row_cols = st.columns(cols_per_row)
            for j, k in enumerate(adjust_keys[i:i + cols_per_row]):
                with row_cols[j]:
                    q = int(retrait_counts.get(k, 0))
                    max_avail = int(close_counts.get(k, 0))

                    st.markdown(f"**{k}**")
                    mcol, pcol = st.columns(2)
                    minus = mcol.button("➖", key=f"r_minus_{k}")
                    plus = pcol.button("➕", key=f"r_plus_{k}")

                    st.write(f"Retrait: **{q}**")
                    st.caption(f"Dispo: {max_avail}")

                    if minus or plus:
                        new_locked = dict(st.session_state.locked_retrait)
                        if k not in new_locked:
                            new_locked[k] = q
                        if minus:
                            new_locked[k] = int(new_locked[k]) - 1
                        if plus:
                            new_locked[k] = int(new_locked[k]) + 1
                        if new_locked[k] < 0:
                            new_locked[k] = 0
                        if new_locked[k] > max_avail:
                            new_locked[k] = max_avail
                        st.session_state.locked_retrait = new_locked
                        st.rerun()

        restant_counts = sub_counts(close_counts, retrait_counts)
        st.divider()
        st.info("RESTANT total (après retrait): " + cents_to_str(total_cents(restant_counts)))

    reg_calc_rows = rows_calc_table(open_counts, close_counts, retrait_counts, restant_counts)

# ================== TAB 2: CHANGE BOX ==================
with tab2:
    st.subheader("Boîte de monnaie — état actuel")
    st.caption("Ici on gère la boîte de change. Priorité: petites dénominations (pièces/rouleaux/10/5).")

    edited_box = st.data_editor(
        st.session_state.df_changebox,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Dénomination": st.column_config.TextColumn(disabled=True),
            "Boîte (actuel)": st.column_config.NumberColumn(min_value=0, step=1),
            "Autorisé boîte": st.column_config.CheckboxColumn(),
        },
        height=520
    )
    st.session_state.df_changebox = edited_box

    box_now = dict_from_df(edited_box, "Boîte (actuel)")
    allowed_box = [r["Dénomination"] for _, r in edited_box.iterrows() if bool(r["Autorisé boîte"])]

    st.session_state.box_target = st.number_input(
        "Montant cible dans la boîte ($) (optionnel)",
        min_value=0,
        step=10,
        value=int(st.session_state.box_target),
        help="Si tu mets un cible, l'app peut suggérer quoi ajouter/retirer pour s'en approcher."
    )
    box_target_cents = int(st.session_state.box_target) * 100
    box_total = total_cents(box_now)

    s1, s2, s3 = st.columns(3)
    s1.info("TOTAL boîte (actuel): " + cents_to_str(box_total))
    s2.write("**Cible boîte:** " + cents_to_str(box_target_cents))
    delta_box = box_target_cents - box_total
    s3.write("**Écart (cible - actuel):** " + cents_to_str(delta_box))

    st.divider()
    st.subheader("Connexion avec la caisse")
    st.caption("Source principale: le RESTANT de la caisse (après retrait) depuis l’onglet 1.")

    # We use restant_counts from tab1 calculations (already computed in this run)
    register_available_for_box = restant_counts  # what's left in register after retrait
    st.write("Dispo côté caisse (après retrait): **" + cents_to_str(total_cents(register_available_for_box)) + "**")

    # Determine actions:
    # If box below target: need to transfer TO box from register
    # If box above target: need to remove FROM box (take out)
    to_box = {k: 0 for k in DENOMS}
    from_box = {k: 0 for k in DENOMS}
    rem_to = 0
    rem_from = 0

    cA, cB, cC = st.columns([1.6, 1.6, 3.0])
    with cA:
        if st.button("Proposer transferts (reset ajustements boîte)"):
            st.session_state.locked_to_box = {}
            st.session_state.locked_from_box = {}
            st.rerun()
    with cB:
        if st.button("Réinitialiser ajustements boîte"):
            st.session_state.locked_to_box = {}
            st.session_state.locked_from_box = {}
            st.rerun()
    with cC:
        st.caption("➖/➕ ici verrouille une dénomination pour le mouvement de boîte.")

    # Compute suggestions based on delta
    if not allowed_box:
        st.error("Choisis au moins un type autorisé pour la boîte.")
    else:
        if delta_box > 0:
            # Need to ADD to box from register
            amount = delta_box
            st.write("**Action:** Ajouter à la boîte depuis la caisse: " + cents_to_str(amount))

            st.session_state.locked_to_box = clamp_locked(st.session_state.locked_to_box, register_available_for_box)
            locked = dict(st.session_state.locked_to_box)

            to_box, rem_to = suggest_changebox(amount, allowed_box, register_available_for_box, locked)

            moved = total_cents(to_box)
            if rem_to == 0:
                st.success("À transférer vers boîte: " + cents_to_str(moved))
            elif rem_to < 0:
                st.warning("Transfert dépasse de " + cents_to_str(-rem_to) + " (verrouillage trop haut).")
            else:
                st.warning("Impossible exact. Reste: " + cents_to_str(rem_to))

            # Adjustment grid (to_box)
            st.markdown("### Ajuster le **transfert vers la boîte**")
            adjust_keys = [k for k in DISPLAY_ORDER if k in allowed_box]
            cols_per_row = 4
            for i in range(0, len(adjust_keys), cols_per_row):
                row_cols = st.columns(cols_per_row)
                for j, k in enumerate(adjust_keys[i:i + cols_per_row]):
                    with row_cols[j]:
                        q = int(to_box.get(k, 0))
                        max_avail = int(register_available_for_box.get(k, 0))

                        st.markdown(f"**{k}**")
                        mcol, pcol = st.columns(2)
                        minus = mcol.button("➖", key=f"tb_minus_{k}")
                        plus = pcol.button("➕", key=f"tb_plus_{k}")

                        st.write(f"Vers boîte: **{q}**")
                        st.caption(f"Dispo caisse: {max_avail}")

                        if minus or plus:
                            new_locked = dict(st.session_state.locked_to_box)
                            if k not in new_locked:
                                new_locked[k] = q
                            if minus:
                                new_locked[k] = int(new_locked[k]) - 1
                            if plus:
                                new_locked[k] = int(new_locked[k]) + 1
                            if new_locked[k] < 0:
                                new_locked[k] = 0
                            if new_locked[k] > max_avail:
                                new_locked[k] = max_avail
                            st.session_state.locked_to_box = new_locked
                            st.rerun()

        elif delta_box < 0:
            # Need to REMOVE from box
            amount = -delta_box
            st.write("**Action:** Retirer de la boîte: " + cents_to_str(amount))

            st.session_state.locked_from_box = clamp_locked(st.session_state.locked_from_box, box_now)
            locked = dict(st.session_state.locked_from_box)

            from_box, rem_from = suggest_changebox(amount, allowed_box, box_now, locked)

            moved = total_cents(from_box)
            if rem_from == 0:
                st.success("À retirer de la boîte: " + cents_to_str(moved))
            elif rem_from < 0:
                st.warning("Retrait dépasse de " + cents_to_str(-rem_from) + " (verrouillage trop haut).")
            else:
                st.warning("Impossible exact. Reste: " + cents_to_str(rem_from))

            st.markdown("### Ajuster le **retrait depuis la boîte**")
            adjust_keys = [k for k in DISPLAY_ORDER if k in allowed_box]
            cols_per_row = 4
            for i in range(0, len(adjust_keys), cols_per_row):
                row_cols = st.columns(cols_per_row)
                for j, k in enumerate(adjust_keys[i:i + cols_per_row]):
                    with row_cols[j]:
                        q = int(from_box.get(k, 0))
                        max_avail = int(box_now.get(k, 0))

                        st.markdown(f"**{k}**")
                        mcol, pcol = st.columns(2)
                        minus = mcol.button("➖", key=f"fb_minus_{k}")
                        plus = pcol.button("➕", key=f"fb_plus_{k}")

                        st.write(f"Depuis boîte: **{q}**")
                        st.caption(f"Dispo boîte: {max_avail}")

                        if minus or plus:
                            new_locked = dict(st.session_state.locked_from_box)
                            if k not in new_locked:
                                new_locked[k] = q
                            if minus:
                                new_locked[k] = int(new_locked[k]) - 1
                            if plus:
                                new_locked[k] = int(new_locked[k]) + 1
                            if new_locked[k] < 0:
                                new_locked[k] = 0
                            if new_locked[k] > max_avail:
                                new_locked[k] = max_avail
                            st.session_state.locked_from_box = new_locked
                            st.rerun()

        else:
            st.success("Boîte exactement à la cible. Aucun mouvement nécessaire.")

    # Apply movements to compute "after"
    # If to_box used: register loses, box gains
    # If from_box used: box loses (and theoretically register gains, but your boss didn't request that side explicitly)
    box_after = clamp_counts(add_counts(sub_counts(box_now, from_box), to_box))

    box_calc_rows = rows_box_calc_table(box_now, to_box, from_box, box_after)
    st.divider()
    st.info("TOTAL boîte (après mouvements): " + cents_to_str(total_cents(box_after)))

# ================== SAVE + DOWNLOAD ==================
st.divider()
st.subheader("Sauvegarde & reçus")

meta = {
    "Date": st.session_state.active_date.isoformat(),
    "Généré à": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
    "Caisse #": int(st.session_state.meta_register_no),
    "Caissier(ère)": st.session_state.meta_cashier.strip() if st.session_state.meta_cashier.strip() else "—",
    "Cible $": int(st.session_state.meta_target),
    "Note": "Fichiers générés automatiquement par date.",
}

payload = build_payload_for_save(reg_calc_rows, box_calc_rows, meta)
autosave_if_needed(payload)

left, mid, right = st.columns([1.3, 1.3, 3.0])
with left:
    if st.button("Enregistrer maintenant"):
        save_state_for(today, payload)
        export_excel_for(today, payload)
        st.session_state.last_saved_hash = hash_state(payload)
        st.session_state.last_saved_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
with mid:
    st.write("Dernière sauvegarde:", st.session_state.last_saved_at or "—")
with right:
    st.caption("Les fichiers du jour sont stockés dans data/records/ (state JSON + Excel).")

xlsx_path = excel_path_for(today)
json_path = state_path_for(today)

d1, d2 = st.columns(2)

with d1:
    if os.path.exists(xlsx_path):
        with open(xlsx_path, "rb") as f:
            st.download_button(
                label="📄 Télécharger le registre Excel du jour",
                data=f,
                file_name=os.path.basename(xlsx_path),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    else:
        st.warning("Excel du jour non trouvé (pas encore créé).")

with d2:
    if os.path.exists(json_path):
        with open(json_path, "rb") as f:
            st.download_button(
                label="🧾 Télécharger l’état JSON du jour",
                data=f,
                file_name=os.path.basename(json_path),
                mime="application/json",
            )
    else:
        st.warning("État JSON non trouvé (pas encore créé).")
