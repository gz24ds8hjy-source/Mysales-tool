"""
Trello-Anbindung fürs MySales-Cockpit.

Liest drei Boards aus:
- BOARD_3M       -> "2. 3 Monate"          (Phasen-Pipeline für 3-Monats-Klienten)
- BOARD_6_12M    -> "3. Upsell 6+12 Monate" (Betreuungs-Pipeline für 6/12-Monats-Klienten)
- BOARD_BESTAND  -> "Bestand"               (Liste "Ausgelaufen" = gekündigte 3-Monats-Klienten)

Hinweis 6/12-Board: es gibt kein eigenes Feld für "6 oder 12 Monate". Die
Paketlänge wird daher aus der Phase abgeleitet: Karten in "Betreuung 7.-9."
oder "10.-12. Monat" sind sicher 12-Monats-Klienten (6-Monats-Klienten wären
vorher schon in "Betreuung beendet"). Karten in "1.-3." oder "4.-6. Monat"
bleiben uneindeutig (paket_monate=None) -- siehe estimate_paket_laenge().

Benötigte Environment-Variablen (bei Render unter "Environment" eintragen,
NIEMALS im Code oder Git-Repo speichern):
    TRELLO_KEY
    TRELLO_TOKEN
    TRELLO_BOARD_3M
    TRELLO_BOARD_6_12M
    TRELLO_BOARD_BESTAND
    GOOGLE_SHEET_WEBHOOK_URL   (optional, für Verlauf/Trend -- siehe apps_script_verlauf.gs)

Board-ID bekommst du einfach aus der URL: trello.com/b/<DAS_HIER>/boardname
Key + Token holst du dir unter https://trello.com/power-ups/admin
(bzw. https://trello.com/app-key für den Key, Token wird dort generiert).
"""

import os
import re
from datetime import datetime, timezone, date
import requests

TRELLO_KEY = os.environ.get("TRELLO_KEY")
TRELLO_TOKEN = os.environ.get("TRELLO_TOKEN")
BOARD_3M = os.environ.get("TRELLO_BOARD_3M")
BOARD_6_12M = os.environ.get("TRELLO_BOARD_6_12M")
BOARD_BESTAND = os.environ.get("TRELLO_BOARD_BESTAND")

# URL des Google Apps Script "Web Apps"-Endpunkts (siehe apps_script_verlauf.gs).
# Wenn nicht gesetzt, läuft das Dashboard normal weiter, nur ohne Verlauf/Trend.
GOOGLE_SHEET_WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL")

# Ab wie vielen Tagen ohne Aktivität eine Karte als "Stau" markiert wird.
# Per Environment-Variable STAU_SCHWELLE_TAGE überschreibbar, falls 14 Tage
# zu streng/lasch sind.
STAU_SCHWELLE_TAGE = int(os.environ.get("STAU_SCHWELLE_TAGE", "14"))

API_BASE = "https://api.trello.com/1"

# Karten mit diesen Namen sind Vorlagen/Notizen, keine echten Klienten-Karten
TEMPLATE_CARD_NAMES = {"infos", "ab hier pause", "pause"}

# Listen, die keine Klienten-Phasen sind, sondern interne Notizen (z.B. WICHTIG)
NON_CLIENT_LISTS = {"wichtig"}

# Liste auf dem 6/12-Board, die beendete Betreuungen enthält
CHURN_LIST_6_12M = "betreuung beendet"

# Wie die Felder in der Kartenbeschreibung heißen -> normalisierter Key
DESC_FIELD_MAP = {
    "geschäftsführer": "geschaeftsfuehrer",
    "branche": "branche",
    "beginn": "beginn",
    "ende": "ende",
    "pause": "pause",
    "zahlweise": "zahlweise",
    "garantie": "garantie",
    "mitarbeiter": "mitarbeiter",
    "namen der mitarbeiter": "namen_der_mitarbeiter",
    "closer": "closer",
    "setter": "setter",
    "webseite": "webseite",
    "telefonnummer": "telefonnummer",
    "e-mail": "email",
    "grund für abschluss": "grund_fuer_abschluss",
}

# Diese Phasen auf dem 6/12-Board sind nur erreichbar, wenn die Betreuung
# länger als 6 Monate läuft -> daraus lässt sich "mindestens 12 Monate" ableiten.
# Bei "Betreuung 1.-3. Monat" / "4.-6. Monat" bleibt die Paketlänge uneindeutig,
# da dort sowohl 6- als auch 12-Monats-Klienten drinstecken können.
PHASES_IMPLYING_12_MONATE = {"betreuung 7.-9. monat", "betreuung 10.-12. monat"}


def _get(path, **params):
    params["key"] = TRELLO_KEY
    params["token"] = TRELLO_TOKEN
    r = requests.get(f"{API_BASE}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def get_lists(board_id):
    """Alle Listen (Spalten) eines Boards."""
    return _get(f"/boards/{board_id}/lists", fields="name")


def get_cards(board_id):
    """Alle offenen Karten eines Boards inkl. Beschreibung, Labels, Cover, letzte Aktivität."""
    return _get(
        f"/boards/{board_id}/cards",
        fields="name,desc,idList,labels,cover,dateLastActivity",
    )


# Trello speichert eingefügte Links manchmal als Markdown ([text](url "titel"))
# statt als reinen Text -- das hier holt nur den lesbaren Teil raus.
MARKDOWN_LINK_RE = re.compile(r'^\[(.*?)\]\(.*?\)')


def clean_value(value):
    """Entfernt Markdown-Link-Reste und unsichtbare Zeichen (z.B. \u200c)."""
    if not value:
        return ""
    value = value.strip()
    match = MARKDOWN_LINK_RE.match(value)
    if match:
        value = match.group(1)
    return value.strip().strip("\u200c").strip()


def parse_description(desc):
    """
    Zerlegt den 'Geschäftsführer: X / Branche: Y / ...'-Text in ein Dict.
    Unbekannte Zeilen werden ignoriert, fehlende Felder kommen als leerer
    String zurück (nie KeyError, auch wenn eine Karte mal unvollständig ist).
    """
    result = {v: "" for v in DESC_FIELD_MAP.values()}
    for line in (desc or "").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        norm_key = DESC_FIELD_MAP.get(key.strip().lower())
        if norm_key:
            result[norm_key] = clean_value(value)
    return result


def is_client_card(card_name):
    return card_name.strip().lower() not in TEMPLATE_CARD_NAMES


def upsell_flag(card):
    """Blaues Karten-Cover = Upsell geplant (Gelb hat laut Absprache keine Bedeutung)."""
    cover = card.get("cover") or {}
    return cover.get("color") == "blue"


def activity_info(card):
    """
    Liefert Tage seit letzter Trello-Aktivität auf der Karte + ob das über
    der Stau-Schwelle liegt. Trello aktualisiert dateLastActivity bei jedem
    Kommentar, jeder Listenbewegung etc. -- ein guter Proxy dafür, ob ein
    Klient gerade "liegen gelassen" wird.
    """
    raw = card.get("dateLastActivity")
    if not raw:
        return {"tage_seit_aktivitaet": None, "ist_stau": False}
    try:
        last_activity = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return {"tage_seit_aktivitaet": None, "ist_stau": False}
    days = (datetime.now(timezone.utc) - last_activity).days
    return {"tage_seit_aktivitaet": days, "ist_stau": days >= STAU_SCHWELLE_TAGE}


def build_3m_pipeline():
    """
    Pro Klient (Karte) auf dem 3-Monats-Board: Name, aktuelle Phase,
    geparste Kartendetails, Upsell-Flag.
    """
    lists_by_id = {l["id"]: l["name"] for l in get_lists(BOARD_3M)}
    cards = get_cards(BOARD_3M)

    clients = []
    for card in cards:
        if not is_client_card(card["name"]):
            continue
        phase = lists_by_id.get(card["idList"], "Unbekannt")
        if phase.strip().lower() in NON_CLIENT_LISTS:
            continue
        details = parse_description(card.get("desc", ""))
        clients.append({
            "name": card["name"],
            "phase": phase,
            "paket_monate": 3,
            "upsell_geplant": upsell_flag(card),
            **activity_info(card),
            **details,
        })
    return clients


def build_churned():
    """
    Klienten vom Bestand-Board, Liste 'Betreuung abgelaufen' (gekündigte
    3-Monats-Klienten).

    Das Bestand-Board hat tatsächlich mehrere Listen (WICHTIG, Kundenliste,
    3 Monate, 12 Monate, Betreuung abgelaufen) -- nur "Betreuung abgelaufen"
    enthält echte Kündigungen. "3 Monate" und "12 Monate" sind offenbar ein
    separates Archiv/Roster und werden hier bewusst NICHT mitgezählt.
    """
    lists_by_id = {l["id"]: l["name"] for l in get_lists(BOARD_BESTAND)}
    cards = get_cards(BOARD_BESTAND)

    churned = []
    for card in cards:
        if not is_client_card(card["name"]):
            continue
        if lists_by_id.get(card["idList"], "").strip().lower() != "betreuung abgelaufen":
            continue
        details = parse_description(card.get("desc", ""))
        churned.append({"name": card["name"], "herkunft": "3_monate", **details})
    return churned



def estimate_paket_laenge(phase):
    """
    Grobe Ableitung der Paketlänge aus der Phase, solange es kein eigenes
    Trello-Feld dafür gibt. Liefert 12, wenn die Phase das eindeutig
    voraussetzt, sonst None (= unbekannt, 6 oder 12 möglich).
    """
    if phase.strip().lower() in PHASES_IMPLYING_12_MONATE:
        return 12
    return None


def build_6_12m_pipeline():
    """
    Pro Klient (Karte) auf dem 6/12-Monats-Board: Name, aktuelle Betreuungsphase
    (z.B. "Betreuung 1.-3. Monat"), geparste Kartendetails, Upsell-Flag.
    Karten aus der Liste "Betreuung beendet" landen separat in build_churned_6_12m(),
    nicht in der aktiven Pipeline.
    """
    lists_by_id = {l["id"]: l["name"] for l in get_lists(BOARD_6_12M)}
    cards = get_cards(BOARD_6_12M)

    clients = []
    for card in cards:
        if not is_client_card(card["name"]):
            continue
        phase = lists_by_id.get(card["idList"], "Unbekannt")
        if phase.strip().lower() == CHURN_LIST_6_12M:
            continue
        details = parse_description(card.get("desc", ""))
        clients.append({
            "name": card["name"],
            "phase": phase,
            "paket_monate": estimate_paket_laenge(phase),
            "upsell_geplant": upsell_flag(card),
            **activity_info(card),
            **details,
        })
    return clients


def build_churned_6_12m():
    """Klienten vom 6/12-Board, Liste 'Betreuung beendet'."""
    lists_by_id = {l["id"]: l["name"] for l in get_lists(BOARD_6_12M)}
    cards = get_cards(BOARD_6_12M)

    churned = []
    for card in cards:
        if not is_client_card(card["name"]):
            continue
        if lists_by_id.get(card["idList"], "").strip().lower() != CHURN_LIST_6_12M:
            continue
        details = parse_description(card.get("desc", ""))
        churned.append({"name": card["name"], "herkunft": "6_12_monate", **details})
    return churned


def build_dashboard_data():
    """Aggregiert alles fürs Dashboard."""
    pipeline_3m = build_3m_pipeline()
    pipeline_6_12m = build_6_12m_pipeline()
    churned = build_churned() + build_churned_6_12m()

    phasen_3m = {}
    for c in pipeline_3m:
        phasen_3m[c["phase"]] = phasen_3m.get(c["phase"], 0) + 1

    phasen_6_12m = {}
    for c in pipeline_6_12m:
        phasen_6_12m[c["phase"]] = phasen_6_12m.get(c["phase"], 0) + 1

    aktiv_gesamt = len(pipeline_3m) + len(pipeline_6_12m)
    upsell_gesamt = sum(1 for c in pipeline_3m + pipeline_6_12m if c["upsell_geplant"])
    stau_gesamt = sum(1 for c in pipeline_3m + pipeline_6_12m if c["ist_stau"])

    kpi = {
        "aktiv_gesamt": aktiv_gesamt,
        "aktiv_3m": len(pipeline_3m),
        "aktiv_6_12m": len(pipeline_6_12m),
        "im_upsell_gespraech": upsell_gesamt,
        "gekuendigt_gesamt": len(churned),
        "in_stau": stau_gesamt,
    }

    # Beides "best effort": wenn das Sheet (noch) nicht eingerichtet ist oder
    # gerade nicht erreichbar ist, läuft das restliche Dashboard trotzdem
    # ganz normal weiter -- nur eben ohne Verlauf.
    log_snapshot(kpi)
    verlauf = get_verlauf()

    return {
        "klienten_3m": pipeline_3m,
        "klienten_6_12m": pipeline_6_12m,
        "phasen_verteilung_3m": phasen_3m,
        "phasen_verteilung_6_12m": phasen_6_12m,
        "gekuendigt": churned,
        "kpi": kpi,
        "verlauf": verlauf,
    }


def log_snapshot(kpi):
    """
    Schickt die heutigen KPI-Werte ans Google Sheet (siehe apps_script_verlauf.gs).
    Wird bei jedem Dashboard-Aufruf getriggert; das Sheet selbst sorgt dafür,
    dass pro Tag nur eine Zeile entsteht (überschreiben statt doppelt anlegen) --
    es braucht also keinen separaten Cron-Job.
    """
    if not GOOGLE_SHEET_WEBHOOK_URL:
        return
    payload = {"datum": date.today().isoformat(), **kpi}
    try:
        requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, timeout=10)
    except requests.RequestException:
        pass  # Verlaufs-Logging darf das Dashboard nie zum Absturz bringen


def get_verlauf():
    """Holt den bisher gespeicherten Tagesverlauf aus dem Google Sheet."""
    if not GOOGLE_SHEET_WEBHOOK_URL:
        return []
    try:
        r = requests.get(GOOGLE_SHEET_WEBHOOK_URL, timeout=10)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return []