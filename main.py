import os
import base64
import requests
import anthropic
from flask import Flask, render_template_string, request, session, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "mysales-secret-key")

ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY", "")
TRELLO_KEY     = os.environ.get("TRELLO_KEY", "")
TRELLO_TOKEN   = os.environ.get("TRELLO_TOKEN", "")
BOARD_NAMES    = ["2. 3 Monate", "3. Upsell 6+12 Monate"]


# ─── Zoom helpers ─────────────────────────────────────────────────────────────

def zoom_get_token(account_id, client_id, client_secret):
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        "https://zoom.us/oauth/token",
        params={"grant_type": "account_credentials", "account_id": account_id},
        headers={"Authorization": f"Basic {creds}"},
        timeout=10
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise ValueError("Zoom-Token konnte nicht abgerufen werden – bitte Credentials prüfen.")
    return token


def zoom_get_calls(token):
    r = requests.get(
        "https://api.zoom.us/v2/meetings/meeting_summaries",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10
    )
    r.raise_for_status()
    return r.json().get("summaries", [])


def zoom_get_summary(token, meeting_uuid):
    r = requests.get(
        f"https://api.zoom.us/v2/meetings/{meeting_uuid}/meeting_summary",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10
    )
    r.raise_for_status()
    return r.json()


# ─── Trello helpers ───────────────────────────────────────────────────────────

def trello_get_cards():
    boards = requests.get(
        "https://api.trello.com/1/members/me/boards",
        params={"key": TRELLO_KEY, "token": TRELLO_TOKEN},
        timeout=10
    ).json()
    board_ids = [b["id"] for b in boards if b["name"] in BOARD_NAMES]
    cards = []
    for bid in board_ids:
        chunk = requests.get(
            f"https://api.trello.com/1/boards/{bid}/cards",
            params={"key": TRELLO_KEY, "token": TRELLO_TOKEN, "fields": "name,desc,id"},
            timeout=10
        ).json()
        cards.extend(chunk)
    return cards


def normalize(s):
    return s.lower().replace(" ", "").replace("-", "").replace("_", "")


def fmt_date(iso: str) -> str:
    """Convert ISO date string like '2024-01-15T10:00:00Z' to '15.01.2024'."""
    if not iso:
        return ""
    date_part = iso[:10]  # "2024-01-15"
    try:
        y, m, d = date_part.split("-")
        return f"{d}.{m}.{y}"
    except Exception:
        return date_part


# ─── HTML Templates ───────────────────────────────────────────────────────────

BASE_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0b1120;
    min-height: 100vh;
    color: #e2e8f0;
    padding: 40px 16px 60px;
  }
  .container { max-width: 780px; margin: 0 auto; }
  .brand {
    display: flex; align-items: center; gap: 12px;
    margin-bottom: 40px;
  }
  .brand-icon {
    width: 42px; height: 42px;
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.2rem; flex-shrink: 0;
  }
  .brand-name  { font-size: 1.3rem; font-weight: 700; color: #f1f5f9; }
  .brand-sub   { font-size: 0.8rem; color: #64748b; }

  .card {
    background: #151f32;
    border: 1px solid #1e2d45;
    border-radius: 16px;
    padding: 30px 32px;
    margin-bottom: 24px;
  }
  .section-title {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #475569;
    margin-bottom: 20px;
  }

  label { display: block; font-size: 0.875rem; color: #94a3b8; margin-bottom: 6px; }
  input[type=text], input[type=password] {
    width: 100%;
    padding: 11px 14px;
    background: #0b1120;
    border: 1px solid #1e2d45;
    border-radius: 9px;
    color: #f1f5f9;
    font-size: 0.9rem;
    margin-bottom: 16px;
    outline: none;
    transition: border-color 0.2s;
  }
  input:focus { border-color: #6366f1; }

  .btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    padding: 12px 24px;
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    color: white; border: none; border-radius: 9px;
    font-size: 0.95rem; font-weight: 600;
    cursor: pointer; transition: opacity 0.15s, transform 0.1s;
    text-decoration: none;
  }
  .btn:hover   { opacity: 0.88; }
  .btn:active  { transform: scale(0.98); }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  .btn-outline {
    background: transparent;
    border: 1px solid #334155;
    color: #94a3b8;
  }
  .btn-outline:hover { background: #1e2d45; opacity: 1; }

  .alert {
    padding: 12px 16px;
    border-radius: 9px;
    font-size: 0.875rem;
    margin-bottom: 20px;
  }
  .alert-error  { background: #2d0e0e; border: 1px solid #7f1d1d; color: #fca5a5; }
  .alert-ok     { background: #052e16; border: 1px solid #14532d; color: #86efac; }

  .call-list { list-style: none; }
  .call-item {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 16px;
    border: 1px solid #1e2d45;
    border-radius: 10px;
    margin-bottom: 10px;
    transition: background 0.15s, border-color 0.15s;
    cursor: pointer;
  }
  .call-item:hover { background: #1a2a40; border-color: #334155; }
  .call-item.selected { background: #1a1f3a; border-color: #6366f1; }
  .call-topic { font-size: 0.9rem; font-weight: 500; color: #e2e8f0; }
  .call-date  { font-size: 0.78rem; color: #64748b; margin-top: 3px; }
  .call-check {
    width: 20px; height: 20px; flex-shrink: 0;
    border: 2px solid #334155; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    transition: all 0.15s;
  }
  .call-item.selected .call-check {
    background: #6366f1; border-color: #6366f1; color: white;
    font-size: 0.7rem;
  }

  .spinner {
    width: 16px; height: 16px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: white;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    display: none;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .result-panel { display: none; }
  .result-header {
    display: flex; align-items: center; gap: 10px;
    padding: 14px 18px;
    background: #0b1120;
    border-bottom: 1px solid #1e2d45;
    border-radius: 10px 10px 0 0;
  }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
  .dot-green { background: #22c55e; }
  .dot-red   { background: #ef4444; }
  .result-title { font-size: 0.875rem; font-weight: 600; }
  .result-body {
    padding: 22px;
    white-space: pre-wrap;
    font-size: 0.875rem;
    line-height: 1.75;
    color: #cbd5e1;
    background: #0f1825;
    border-radius: 0 0 10px 10px;
    border: 1px solid #1e2d45;
    border-top: none;
  }
  .trello-tag {
    display: inline-block; margin-top: 14px;
    padding: 5px 14px; border-radius: 20px;
    font-size: 0.78rem;
  }
  .trello-tag.ok    { background: #0c2a4a; border: 1px solid #1d4ed8; color: #93c5fd; }
  .trello-tag.fail  { background: #2d0e0e; border: 1px solid #7f1d1d; color: #fca5a5; }

  .override-panel {
    display: none;
    margin-top: 20px;
    padding: 20px 22px;
    background: #1a1a2e;
    border: 1px solid #f59e0b55;
    border-radius: 12px;
  }
  .override-title {
    font-size: 0.8rem;
    font-weight: 600;
    color: #f59e0b;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 12px;
  }
  .override-hint {
    font-size: 0.82rem;
    color: #94a3b8;
    margin-bottom: 14px;
    line-height: 1.5;
  }
  select.card-select {
    width: 100%;
    padding: 10px 14px;
    background: #0b1120;
    border: 1px solid #334155;
    border-radius: 9px;
    color: #f1f5f9;
    font-size: 0.875rem;
    margin-bottom: 14px;
    outline: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' fill='%2394a3b8' viewBox='0 0 16 16'%3E%3Cpath d='M7.247 11.14L2.451 5.658C1.885 5.013 2.345 4 3.204 4h9.592a1 1 0 0 1 .753 1.659l-4.796 5.48a1 1 0 0 1-1.506 0z'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    cursor: pointer;
  }
  select.card-select:focus { border-color: #f59e0b; }
  select.card-select option { background: #151f32; }
  .btn-amber {
    background: linear-gradient(135deg, #d97706, #f59e0b);
  }

  /* ── Searchable card combobox ── */
  .card-search-wrap { position: relative; }
  .card-search-input {
    width: 100%;
    padding: 10px 36px 10px 14px;
    background: #0b1120;
    border: 1px solid #334155;
    border-radius: 9px;
    color: #f1f5f9;
    font-size: 0.875rem;
    outline: none;
    transition: border-color 0.2s;
    margin-bottom: 0;
  }
  .card-search-input:focus { border-color: #f59e0b; }
  .card-search-input::placeholder { color: #475569; }
  .card-dropdown {
    display: none;
    position: absolute;
    top: calc(100% + 4px);
    left: 0; right: 0;
    background: #151f32;
    border: 1px solid #334155;
    border-radius: 9px;
    max-height: 220px;
    overflow-y: auto;
    z-index: 100;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }
  .card-dropdown.open { display: block; }
  .card-option {
    padding: 10px 14px;
    font-size: 0.875rem;
    color: #cbd5e1;
    cursor: pointer;
    transition: background 0.1s;
  }
  .card-option:hover, .card-option.highlighted { background: #1e3a5f; color: #f1f5f9; }
  .card-option.no-result { color: #64748b; cursor: default; font-style: italic; }
  .card-option.no-result:hover { background: transparent; }
  .card-selected-name {
    margin-top: 8px;
    font-size: 0.8rem;
    color: #f59e0b;
    min-height: 18px;
  }
</style>
"""

# ─── Page 1: Zoom credentials form ────────────────────────────────────────────

PAGE_LOGIN = BASE_STYLE + """
<div class="container">
  <div class="brand">
    <div class="brand-icon">📞</div>
    <div>
      <div class="brand-name">MySales Call-Tool</div>
      <div class="brand-sub">Zoom · Claude · Trello</div>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Zoom Zugangsdaten</div>
    {% if error %}
    <div class="alert alert-error">{{ error }}</div>
    {% endif %}
    <form method="POST" action="/calls">
      <label>Account ID</label>
      <input type="text" name="account_id" placeholder="z.B. 7mkSTPjWQEOkM3-S3xOp8g" value="{{ prev.account_id }}" required>
      <label>Client ID</label>
      <input type="text" name="client_id" placeholder="z.B. LF6V4UAT7Cj..." value="{{ prev.client_id }}" required>
      <label>Client Secret</label>
      <input type="password" name="client_secret" placeholder="Client Secret" required>
      <button type="submit" class="btn" style="width:100%;margin-top:4px;">
        Calls laden →
      </button>
    </form>
  </div>
</div>
"""

# ─── Page 2: Call list ─────────────────────────────────────────────────────────

PAGE_CALLS = BASE_STYLE + """
<div class="container">
  <div class="brand">
    <div class="brand-icon">📞</div>
    <div>
      <div class="brand-name">MySales Call-Tool</div>
      <div class="brand-sub">Zoom · Claude · Trello</div>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Zoom Call auswählen ({{ calls|length }} Calls gefunden)</div>
    <ul class="call-list" id="call-list">
    {% for call in calls %}
      <li class="call-item" onclick="select('{{ call.uuid }}', this)">
        <div>
          <div class="call-topic">{{ call.topic }}</div>
          <div class="call-date">{{ call.date }}</div>
        </div>
        <div class="call-check" id="check-{{ loop.index }}">✓</div>
      </li>
    {% endfor %}
    </ul>

    <div style="display:flex;gap:10px;margin-top:20px;">
      <a href="/" class="btn btn-outline">← Zurück</a>
      <button id="save-btn" class="btn" onclick="saveSummary()" disabled style="flex:1">
        <div class="spinner" id="spinner"></div>
        <span id="btn-text">Zusammenfassung in Trello speichern</span>
      </button>
    </div>
  </div>

  <div class="result-panel" id="result-panel">
    <div class="result-header">
      <div class="dot" id="result-dot"></div>
      <span class="result-title" id="result-title"></span>
    </div>
    <div class="result-body" id="result-body">
      <!-- Override panel shown when no Trello card found automatically -->
      <div class="override-panel" id="override-panel">
        <div class="override-title">⚠ Karte nicht gefunden — manuell auswählen</div>
        <div class="override-hint" id="override-hint"></div>
        <div class="card-search-wrap" id="card-search-wrap">
          <input type="text" class="card-search-input" id="card-search-input"
            placeholder="Kundenname tippen zum Filtern …"
            autocomplete="off"
            oninput="filterCards()"
            onfocus="openDropdown()"
          >
          <div class="card-dropdown" id="card-dropdown"></div>
          <input type="hidden" id="selected-card-id">
          <div class="card-selected-name" id="card-selected-name"></div>
        </div>
        <button class="btn btn-amber" onclick="postManually()" style="width:100%;margin-top:14px" disabled id="override-save-btn">
          <div class="spinner" id="override-spinner"></div>
          <span id="override-btn-text">In Trello speichern</span>
        </button>
      </div>
    </div>
  </div>
</div>

<script>
let selectedUuid  = null;
let pendingSummary = null;
let allCards       = [];   // [{id, name}] populated when override shown

// ── Call selection ────────────────────────────────────────────────────────────
function select(uuid, el) {
  document.querySelectorAll('.call-item').forEach(i => i.classList.remove('selected'));
  el.classList.add('selected');
  selectedUuid = uuid;
  document.getElementById('save-btn').disabled = false;
  document.getElementById('result-panel').style.display = 'none';
  document.getElementById('override-panel').style.display = 'none';
  pendingSummary = null;
}

// ── Result helpers ────────────────────────────────────────────────────────────
function showResult(dot, title, bodyText) {
  const body = document.getElementById('result-body');
  Array.from(body.childNodes).forEach(n => { if (n.nodeType === 3) body.removeChild(n); });
  const oldTag = body.querySelector('.trello-tag');
  if (oldTag) oldTag.remove();
  body.insertAdjacentText('afterbegin', bodyText);
  document.getElementById('result-dot').className   = 'dot ' + dot;
  document.getElementById('result-title').textContent = title;
  const panel = document.getElementById('result-panel');
  panel.style.display = 'block';
  panel.scrollIntoView({ behavior: 'smooth' });
}

function appendTag(cls, text) {
  const tag = document.createElement('div');
  tag.className   = 'trello-tag ' + cls;
  tag.textContent = text;
  const body = document.getElementById('result-body');
  body.insertBefore(tag, document.getElementById('override-panel'));
}

// ── Searchable combobox ───────────────────────────────────────────────────────
function openDropdown() {
  filterCards();
  document.getElementById('card-dropdown').classList.add('open');
}

function filterCards() {
  const q   = document.getElementById('card-search-input').value.toLowerCase().trim();
  const dd  = document.getElementById('card-dropdown');
  const matches = q
    ? allCards.filter(c => c.name.toLowerCase().includes(q))
    : allCards;
  if (matches.length === 0) {
    dd.innerHTML = '<div class="card-option no-result">Keine Karte gefunden</div>';
  } else {
    dd.innerHTML = matches.map(c =>
      `<div class="card-option" data-id="${c.id}" data-name="${c.name.replace(/"/g,'&quot;')}"
            onclick="pickCard('${c.id}', this.dataset.name)">${c.name}</div>`
    ).join('');
  }
  dd.classList.add('open');
}

function pickCard(id, name) {
  document.getElementById('selected-card-id').value   = id;
  document.getElementById('card-search-input').value  = name;
  document.getElementById('card-selected-name').textContent = '';
  document.getElementById('card-dropdown').classList.remove('open');
  document.getElementById('override-save-btn').disabled = false;
}

// Close dropdown when clicking outside
document.addEventListener('click', e => {
  const wrap = document.getElementById('card-search-wrap');
  if (wrap && !wrap.contains(e.target)) {
    document.getElementById('card-dropdown').classList.remove('open');
  }
});

// ── Main save ─────────────────────────────────────────────────────────────────
async function saveSummary() {
  if (!selectedUuid) return;
  const btn     = document.getElementById('save-btn');
  const spinner = document.getElementById('spinner');
  const btnText = document.getElementById('btn-text');
  btn.disabled         = true;
  spinner.style.display = 'block';
  btnText.textContent  = 'Wird verarbeitet …';
  document.getElementById('override-panel').style.display = 'none';

  try {
    const resp = await fetch('/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ uuid: selectedUuid })
    });
    const data = await resp.json();

    if (data.error) { showResult('dot-red', 'Fehler', data.error); return; }

    pendingSummary = data.summary;
    showResult('dot-green', 'Zusammenfassung — ' + data.customer, data.summary);

    if (data.trello_ok) {
      appendTag('ok', '✓ Trello: ' + data.trello_card);
    } else if (data.needs_override) {
      allCards = data.all_cards;
      document.getElementById('override-hint').textContent =
        'Kein automatischer Treffer fuer "' + data.customer + '". Karte tippen und auswaehlen:';
      // Reset combobox
      document.getElementById('card-search-input').value      = '';
      document.getElementById('selected-card-id').value       = '';
      document.getElementById('card-selected-name').textContent = '';
      document.getElementById('override-save-btn').disabled   = true;
      document.getElementById('card-dropdown').classList.remove('open');
      document.getElementById('override-panel').style.display = 'block';
    } else {
      appendTag('fail', '✗ ' + data.trello_msg);
    }
  } catch(e) {
    showResult('dot-red', 'Netzwerkfehler', e.message);
  } finally {
    btn.disabled          = false;
    spinner.style.display = 'none';
    btnText.textContent   = 'Zusammenfassung in Trello speichern';
  }
}

// ── Manual card post ──────────────────────────────────────────────────────────
async function postManually() {
  const cardId = document.getElementById('selected-card-id').value;
  if (!cardId || !pendingSummary) return;

  const btn = document.getElementById('override-save-btn');
  const sp  = document.getElementById('override-spinner');
  const bt  = document.getElementById('override-btn-text');
  btn.disabled          = true;
  sp.style.display      = 'block';
  bt.textContent        = 'Wird gespeichert …';

  try {
    const resp = await fetch('/post_to_card', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ card_id: cardId, summary: pendingSummary })
    });
    const data = await resp.json();

    document.getElementById('override-panel').style.display = 'none';
    const oldTag = document.querySelector('#result-body .trello-tag');
    if (oldTag) oldTag.remove();

    if (data.ok) {
      appendTag('ok', '✓ Trello: ' + data.card_name);
    } else {
      appendTag('fail', '✗ Fehler: ' + data.error);
      btn.disabled = false;
    }
  } catch(e) {
    appendTag('fail', '✗ Netzwerkfehler: ' + e.message);
    btn.disabled = false;
  } finally {
    sp.style.display = 'none';
    bt.textContent   = 'In Trello speichern';
  }
}
</script>
"""


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    error = session.pop("login_error", None)
    prev = {
        "account_id": session.get("zoom_account_id", ""),
        "client_id": session.get("zoom_client_id", ""),
    }
    return render_template_string(PAGE_LOGIN, error=error, prev=prev)


@app.route("/calls", methods=["GET", "POST"])
def calls():
    from flask import redirect, url_for

    if request.method == "GET":
        # Reload from session (e.g. after browser refresh)
        call_list = session.get("call_list")
        if not call_list:
            return redirect(url_for("index"))
        return render_template_string(PAGE_CALLS, calls=call_list)

    # POST: fresh login
    account_id    = request.form.get("account_id", "").strip()
    client_id     = request.form.get("client_id", "").strip()
    client_secret = request.form.get("client_secret", "").strip()

    try:
        token = zoom_get_token(account_id, client_id, client_secret)
    except Exception as e:
        session["login_error"] = str(e)
        session["zoom_account_id"] = account_id
        session["zoom_client_id"]  = client_id
        return redirect(url_for("index"))

    # Store credentials for /save route
    session["zoom_account_id"]    = account_id
    session["zoom_client_id"]     = client_id
    session["zoom_client_secret"] = client_secret
    session["zoom_token"]         = token

    try:
        raw = zoom_get_calls(token)
    except Exception as e:
        session["login_error"] = f"Calls konnten nicht geladen werden: {e}"
        return redirect(url_for("index"))

    call_list = [
        {
            "uuid":  c.get("meeting_uuid", ""),
            "topic": c.get("meeting_topic", "Unbekannt"),
            "date":  fmt_date(c.get("meeting_start_time") or ""),
        }
        for c in raw[:30]
    ]
    session["call_list"] = call_list

    return redirect(url_for("calls"))


@app.route("/save", methods=["POST"])
def save():
    data = request.get_json()
    meeting_uuid = data.get("uuid", "")
    if not meeting_uuid:
        return jsonify({"error": "Keine Meeting-UUID."})

    token = session.get("zoom_token")
    if not token:
        # Re-authenticate
        try:
            token = zoom_get_token(
                session.get("zoom_account_id", ""),
                session.get("zoom_client_id", ""),
                session.get("zoom_client_secret", ""),
            )
            session["zoom_token"] = token
        except Exception as e:
            return jsonify({"error": f"Zoom-Authentifizierung fehlgeschlagen: {e}"})

    # Look up meeting_start_time from the session call list (correct field)
    call_list = session.get("call_list", [])
    call_entry = next((c for c in call_list if c.get("uuid") == meeting_uuid), None)
    # call_entry["date"] is already fmt_date'd; keep it as fallback
    session_date = call_entry["date"] if call_entry else ""

    try:
        detail = zoom_get_summary(token, meeting_uuid)
        summary_content = detail.get("summary_overview", "")
        next_steps      = str(detail.get("next_steps", ""))
        summary_content = summary_content.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
        next_steps = next_steps.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
        # Prefer meeting_start_time from detail if present, else fall back to session date
        raw_start = detail.get("meeting_start_time") or detail.get("start_time") or ""
        meeting_date = fmt_date(raw_start) if raw_start else session_date
        zoom_text       = f"Zusammenfassung: {summary_content}\nNächste Schritte: {next_steps}"
    except Exception as e:
        return jsonify({"error": f"Zoom-Zusammenfassung konnte nicht geladen werden: {e}"})

    try:
        alle_karten = trello_get_cards()
    except Exception as e:
        return jsonify({"error": f"Trello-Karten konnten nicht geladen werden: {e}"})

    claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    # 1) Kundenname extrahieren
    try:
        erkannter_name = claude.messages.create(
            model="claude-opus-4-5",
            max_tokens=50,
            messages=[{"role": "user", "content":
                f"Extrahiere den Firmennamen des KUNDEN (nicht René Poschmann, nicht MySales, nicht Stefan) "
                f"aus diesem Text. Wenn kein Firmenname genannt wird, nimm den Vornamen des Kunden. "
                f"Antworte NUR mit einem einzigen Namen:\n\n{zoom_text[:1000]}"}]
        ).content[0].text.strip()
    except Exception as e:
        return jsonify({"error": f"Claude (Kundenerkennung) Fehler: {e}"})

    # 2) Trello-Karte finden
    card_id   = None
    card_name = ""
    for c in alle_karten:
        n = normalize(erkannter_name)
        if n in normalize(c["name"]) or normalize(c["name"]) in n or n in normalize(c.get("desc", "")):
            card_id   = c["id"]
            card_name = c["name"]
            break

    # 3) Zusammenfassung erstellen
    try:
        zusammenfassung = claude.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content":
                f"Erstelle eine kurze strukturierte Call-Zusammenfassung auf Deutsch. "
                f"Datum: {meeting_date}. "
                f"Format: Datum, Kunde, Stand, Besprochene Themen, Nächste Schritte, Nächster Call. "
                f"Inhalt: {zoom_text}"}]
        ).content[0].text
        zusammenfassung = zusammenfassung.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
        zeilen = zusammenfassung.split('\n')
        bereinigte_zeilen = []
        for zeile in zeilen:
            if zeile.strip().startswith('|'):
                inhalt = zeile.replace('|', ' ').replace('**', '').replace('#', '').strip()
                if inhalt and not all(c in '-: ' for c in inhalt):
                    bereinigte_zeilen.append(inhalt)
            else:
                bereinigte_zeilen.append(zeile.replace('**', '').replace('# ', ''))
        zusammenfassung = '\n'.join(bereinigte_zeilen)
    except Exception as e:
        return jsonify({"error": f"Claude (Zusammenfassung) Fehler: {e}"})

    # 4) In Trello posten
    trello_ok  = False
    trello_msg = ""
    if card_id:
        try:
            r = requests.post(
                f"https://api.trello.com/1/cards/{card_id}/actions/comments",
                params={"key": TRELLO_KEY, "token": TRELLO_TOKEN, "text": zusammenfassung},
                timeout=10
            )
            trello_ok  = r.status_code == 200
            trello_msg = f"HTTP {r.status_code}" if not trello_ok else ""
        except Exception as e:
            trello_msg = str(e)
        return jsonify({
            "summary":     zusammenfassung,
            "customer":    erkannter_name,
            "trello_ok":   trello_ok,
            "trello_card": card_name,
            "trello_msg":  trello_msg,
        })
    else:
        # No automatic match — return card list for manual override
        return jsonify({
            "summary":       zusammenfassung,
            "customer":      erkannter_name,
            "trello_ok":     False,
            "needs_override": True,
            "trello_msg":    f"Kein Treffer fuer '{erkannter_name}'",
            "all_cards":     [{"id": c["id"], "name": c["name"]} for c in alle_karten],
        })


@app.route("/post_to_card", methods=["POST"])
def post_to_card():
    data    = request.get_json()
    card_id = data.get("card_id", "")
    summary = data.get("summary", "")
    if not card_id or not summary:
        return jsonify({"ok": False, "error": "Fehlende Parameter."})
    try:
        r = requests.post(
            f"https://api.trello.com/1/cards/{card_id}/actions/comments",
            params={"key": TRELLO_KEY, "token": TRELLO_TOKEN, "text": summary},
            timeout=10
        )
        if r.status_code == 200:
            # Fetch card name for confirmation
            card = requests.get(
                f"https://api.trello.com/1/cards/{card_id}",
                params={"key": TRELLO_KEY, "token": TRELLO_TOKEN, "fields": "name"},
                timeout=10
            ).json()
            return jsonify({"ok": True, "card_name": card.get("name", card_id)})
        else:
            return jsonify({"ok": False, "error": f"Trello HTTP {r.status_code}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
