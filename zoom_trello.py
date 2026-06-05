# coding: utf-8
import anthropic
import requests
from datetime import datetime
import base64

# Keys
import os

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
TRELLO_KEY = os.environ.get("TRELLO_KEY")
TRELLO_TOKEN = os.environ.get("TRELLO_TOKEN")
BOARD_NAMES = ["2. 3 Monate", "3. Upsell 6+12 Monate"]
ZOOM_ACCOUNT_ID = os.environ.get("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.environ.get("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.environ.get("ZOOM_CLIENT_SECRET")

# Zoom Token
credentials = base64.b64encode(f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}".encode()).decode()
zoom_token = requests.post(
    "https://zoom.us/oauth/token",
    params={"grant_type": "account_credentials", "account_id": ZOOM_ACCOUNT_ID},
    headers={"Authorization": f"Basic {credentials}"}
).json().get("access_token")

# Zoom Calls auflisten
summaries = requests.get(
    "https://api.zoom.us/v2/meetings/meeting_summaries",
    headers={"Authorization": f"Bearer {zoom_token}"}
).json()

alle_calls = summaries["summaries"]
print("\nVerfuegbare Calls:")
for i, call in enumerate(alle_calls):
    print(f"{i+1}. {call.get('meeting_topic', 'Unbekannt')} - {call.get('meeting_start_time', '')[:10]}")

auswahl = int(input("\nWelchen Call? (Nummer): ")) - 1
neuester = alle_calls[auswahl]
meeting_uuid = neuester.get("meeting_uuid", "")
meeting_date = neuester.get("meeting_start_time", "")[:10]

# Zoom Zusammenfassung holen
detail = requests.get(
    f"https://api.zoom.us/v2/meetings/{meeting_uuid}/meeting_summary",
    headers={"Authorization": f"Bearer {zoom_token}"}
).json()
summary_content = detail.get("summary_overview", "")
next_steps = str(detail.get("next_steps", ""))
zoom_text = f"Zusammenfassung: {summary_content} Naechste Schritte: {next_steps}"

# Alle Trello Karten laden
boards = requests.get(
    "https://api.trello.com/1/members/me/boards",
    params={"key": TRELLO_KEY, "token": TRELLO_TOKEN}
).json()
board_ids = [b["id"] for b in boards if b["name"] in BOARD_NAMES]

alle_karten = []
for bid in board_ids:
    karten = requests.get(
        f"https://api.trello.com/1/boards/{bid}/cards",
        params={"key": TRELLO_KEY, "token": TRELLO_TOKEN, "fields": "name,desc,id"}
    ).json()
    alle_karten.extend(karten)

# Claude extrahiert Kundenname
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
erkannter_name = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=50,
    messages=[{"role": "user", "content": f"Extrahiere den Firmennamen des KUNDEN (nicht René Poschmann, nicht MySales, nicht Stefan) aus diesem Text. Wenn kein Firmenname genannt wird, nimm den Vornamen des Kunden. Antworte NUR mit einem einzigen Namen:\n\n{zoom_text[:1000]}"}] 
).content[0].text.strip()

print(f"\nErkannter Kunde: {erkannter_name}")

# Karte suchen
def normalize(s):
    return s.lower().replace(" ", "").replace("-", "").replace("_", "")

card_id = next((c["id"] for c in alle_karten if normalize(erkannter_name) in normalize(c["name"]) or normalize(c["name"]) in normalize(erkannter_name) or normalize(erkannter_name) in normalize(c.get("desc", ""))), None)

if card_id:
    karten_name = next(c["name"] for c in alle_karten if c["id"] == card_id)
    print(f"Trello Karte gefunden: {karten_name}")
    bestaetigung = input("Stimmt das? (j/n): ")
    if bestaetigung.lower() != "j":
        card_id = None

if not card_id:
    kunde = input("Kundenname manuell eingeben: ")
    card_id = next((c["id"] for c in alle_karten if kunde.lower() in c["name"].lower()), None)
    if not card_id:
        print("Keine Karte gefunden!")
        exit()

# Claude erstellt Zusammenfassung
zusammenfassung = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": f"Erstelle eine kurze strukturierte Call-Zusammenfassung auf Deutsch. Datum: {meeting_date}. Format: Datum, Kunde, Stand, Besprochene Themen, Naechste Schritte, Naechster Call. Inhalt: {zoom_text}"}]
).content[0].text

# In Trello posten
r = requests.post(
    f"https://api.trello.com/1/cards/{card_id}/actions/comments",
    params={"key": TRELLO_KEY, "token": TRELLO_TOKEN, "text": zusammenfassung}
)
print(f"Status: {r.status_code}")
print("Fertig! Zusammenfassung in Trello gespeichert.")