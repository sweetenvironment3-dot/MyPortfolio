import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from datetime import datetime

from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, render_template, request, jsonify, g, session, send_from_directory, abort

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# ---------------------------------------------------------------------------
# Production configuration (all of it can be set with environment variables)
#   PORT           port to listen on                      (default 3000)
#   FLASK_DEBUG    "1" turns debug mode on - LOCAL ONLY   (default off)
#   DATABASE_PATH  where the SQLite file lives            (default ./instance/portfolio.db)
#   SECRET_KEY     signing key for cookies/sessions       (default: generated + saved next to the DB)
#   TRUST_PROXY    how many reverse proxies sit in front  (default 1)
# ---------------------------------------------------------------------------
DEBUG = os.environ.get("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")
PORT = int(os.environ.get("PORT", "3000"))
DB_PATH = os.environ.get("DATABASE_PATH") or os.path.join(BASE_DIR, "instance", "portfolio.db")
DATA_DIR = os.path.dirname(os.path.abspath(DB_PATH))
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("myportfolio")


def load_secret_key():
    """Use SECRET_KEY if set; otherwise create a random one once and keep it
    on disk so every worker and every restart uses the same key."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(DATA_DIR, ".secret_key")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            saved = fh.read().strip()
            if saved:
                return saved
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key)
    except OSError:
        pass  # another worker won the race or the disk is read-only; fine
    return key


app = Flask(__name__)
# Behind the host's reverse proxy: trust the forwarded scheme/host/client IP so
# shared links and social preview images use https and the real domain.
_proxies = int(os.environ.get("TRUST_PROXY", "1"))
if _proxies > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_proxies, x_proto=_proxies, x_host=_proxies)
app.secret_key = load_secret_key()
app.config.update(
    DEBUG=DEBUG,
    MAX_CONTENT_LENGTH=32 * 1024,          # forms and heartbeats are tiny; reject anything bigger
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# A visitor is considered "live" if we've heard from it in the last
# LIVE_WINDOW_SECONDS. The front end pings /api/heartbeat every
# HEARTBEAT_INTERVAL_MS (see main.js) to keep itself marked live.
LIVE_WINDOW_SECONDS = 25

# Input limits (stop oversized or junk submissions from filling the database)
MAX_NAME_LEN = 80
MAX_MESSAGE_LEN = 1000
MAX_PAGE_LEN = 200
VISITOR_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


# ---------------------------------------------------------------------------
# Tiny in-memory rate limiter (per worker, per client IP). Enough to blunt
# spam on the comment form and the heartbeat without extra dependencies.
# ---------------------------------------------------------------------------
_hits = {}
_hits_lock = threading.Lock()


def rate_limited(bucket, limit, window_seconds):
    now = time.time()
    key = (bucket, request.remote_addr or "?")
    with _hits_lock:
        q = _hits.setdefault(key, deque())
        while q and q[0] <= now - window_seconds:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        if len(_hits) > 5000:  # occasional cleanup so the dict cannot grow forever
            for k in [k for k, v in _hits.items() if not v or v[-1] < now - 900]:
                _hits.pop(k, None)
    return False


# ---------------------------------------------------------------------------
# Security headers on every response
# ---------------------------------------------------------------------------
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    # the service worker also fetches Google Fonts, which counts as "connect"
    "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com",
    "manifest-src 'self'",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
])


@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    if request.is_secure and not DEBUG:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH, timeout=15)
    # WAL lets several workers read while one writes (needed with gunicorn).
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            whatsapp TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS visitors (
            visitor_id TEXT PRIMARY KEY,
            first_seen REAL NOT NULL,
            last_seen REAL NOT NULL,
            page TEXT
        )
        """
    )
    db.commit()
    db.close()


init_db()


# ---------------------------------------------------------------------------
# Country dialling codes for the comment form's WhatsApp dropdown
# ---------------------------------------------------------------------------

DEFAULT_COUNTRY_CODE = "234"  # Nigeria

COMMON_COUNTRIES = [
    ("Nigeria", "234"),
    ("Togo", "228"),
    ("Ghana", "233"),
]

OTHER_COUNTRIES = [
    ("Afghanistan", "93"), ("Albania", "355"), ("Algeria", "213"), ("Andorra", "376"),
    ("Angola", "244"), ("Antigua and Barbuda", "1268"), ("Argentina", "54"), ("Armenia", "374"),
    ("Australia", "61"), ("Austria", "43"), ("Azerbaijan", "994"), ("Bahamas", "1242"),
    ("Bahrain", "973"), ("Bangladesh", "880"), ("Barbados", "1246"), ("Belarus", "375"),
    ("Belgium", "32"), ("Belize", "501"), ("Benin", "229"), ("Bhutan", "975"),
    ("Bolivia", "591"), ("Bosnia and Herzegovina", "387"), ("Botswana", "267"), ("Brazil", "55"),
    ("Brunei", "673"), ("Bulgaria", "359"), ("Burkina Faso", "226"), ("Burundi", "257"),
    ("Cambodia", "855"), ("Cameroon", "237"), ("Canada", "1"), ("Cape Verde", "238"),
    ("Central African Republic", "236"), ("Chad", "235"), ("Chile", "56"), ("China", "86"),
    ("Colombia", "57"), ("Comoros", "269"), ("Congo (Brazzaville)", "242"), ("Congo (DR)", "243"),
    ("Costa Rica", "506"), ("Côte d'Ivoire", "225"), ("Croatia", "385"), ("Cuba", "53"),
    ("Cyprus", "357"), ("Czech Republic", "420"), ("Denmark", "45"), ("Djibouti", "253"),
    ("Dominica", "1767"), ("Dominican Republic", "1809"), ("Ecuador", "593"), ("Egypt", "20"),
    ("El Salvador", "503"), ("Equatorial Guinea", "240"), ("Eritrea", "291"), ("Estonia", "372"),
    ("Eswatini", "268"), ("Ethiopia", "251"), ("Fiji", "679"), ("Finland", "358"),
    ("France", "33"), ("Gabon", "241"), ("Gambia", "220"), ("Georgia", "995"),
    ("Germany", "49"), ("Greece", "30"), ("Grenada", "1473"), ("Guatemala", "502"),
    ("Guinea", "224"), ("Guinea-Bissau", "245"), ("Guyana", "592"), ("Haiti", "509"),
    ("Honduras", "504"), ("Hong Kong", "852"), ("Hungary", "36"), ("Iceland", "354"),
    ("India", "91"), ("Indonesia", "62"), ("Iran", "98"), ("Iraq", "964"),
    ("Ireland", "353"), ("Israel", "972"), ("Italy", "39"), ("Jamaica", "1876"),
    ("Japan", "81"), ("Jordan", "962"), ("Kazakhstan", "7"), ("Kenya", "254"),
    ("Kiribati", "686"), ("Kosovo", "383"), ("Kuwait", "965"), ("Kyrgyzstan", "996"),
    ("Laos", "856"), ("Latvia", "371"), ("Lebanon", "961"), ("Lesotho", "266"),
    ("Liberia", "231"), ("Libya", "218"), ("Liechtenstein", "423"), ("Lithuania", "370"),
    ("Luxembourg", "352"), ("Macau", "853"), ("Madagascar", "261"), ("Malawi", "265"),
    ("Malaysia", "60"), ("Maldives", "960"), ("Mali", "223"), ("Malta", "356"),
    ("Marshall Islands", "692"), ("Mauritania", "222"), ("Mauritius", "230"), ("Mexico", "52"),
    ("Micronesia", "691"), ("Moldova", "373"), ("Monaco", "377"), ("Mongolia", "976"),
    ("Montenegro", "382"), ("Morocco", "212"), ("Mozambique", "258"), ("Myanmar", "95"),
    ("Namibia", "264"), ("Nauru", "674"), ("Nepal", "977"), ("Netherlands", "31"),
    ("New Zealand", "64"), ("Nicaragua", "505"), ("Niger", "227"), ("North Korea", "850"),
    ("North Macedonia", "389"), ("Norway", "47"), ("Oman", "968"), ("Pakistan", "92"),
    ("Palau", "680"), ("Palestine", "970"), ("Panama", "507"), ("Papua New Guinea", "675"),
    ("Paraguay", "595"), ("Peru", "51"), ("Philippines", "63"), ("Poland", "48"),
    ("Portugal", "351"), ("Puerto Rico", "1787"), ("Qatar", "974"), ("Romania", "40"),
    ("Russia", "7"), ("Rwanda", "250"), ("Saint Kitts and Nevis", "1869"), ("Saint Lucia", "1758"),
    ("Saint Vincent and the Grenadines", "1784"), ("Samoa", "685"), ("San Marino", "378"),
    ("São Tomé and Príncipe", "239"), ("Saudi Arabia", "966"), ("Senegal", "221"), ("Serbia", "381"),
    ("Seychelles", "248"), ("Sierra Leone", "232"), ("Singapore", "65"), ("Slovakia", "421"),
    ("Slovenia", "386"), ("Solomon Islands", "677"), ("Somalia", "252"), ("South Africa", "27"),
    ("South Korea", "82"), ("South Sudan", "211"), ("Spain", "34"), ("Sri Lanka", "94"),
    ("Sudan", "249"), ("Suriname", "597"), ("Sweden", "46"), ("Switzerland", "41"),
    ("Syria", "963"), ("Taiwan", "886"), ("Tajikistan", "992"), ("Tanzania", "255"),
    ("Thailand", "66"), ("Timor-Leste", "670"), ("Tonga", "676"), ("Trinidad and Tobago", "1868"),
    ("Tunisia", "216"), ("Turkey", "90"), ("Turkmenistan", "993"), ("Tuvalu", "688"),
    ("Uganda", "256"), ("Ukraine", "380"), ("United Arab Emirates", "971"), ("United Kingdom", "44"),
    ("United States", "1"), ("Uruguay", "598"), ("Uzbekistan", "998"), ("Vanuatu", "678"),
    ("Vatican City", "379"), ("Venezuela", "58"), ("Vietnam", "84"), ("Yemen", "967"),
    ("Zambia", "260"), ("Zimbabwe", "263"),
]

VALID_COUNTRY_CODES = {code for _, code in COMMON_COUNTRIES + OTHER_COUNTRIES}


def wa_link(value):
    """Return a wa.me link for a stored number, or None if it isn't a full
    international number (older comments may be free text)."""
    if not value or not value.strip().startswith("+"):
        return None
    digits = re.sub(r"\D", "", value)
    if 7 <= len(digits) <= 15:
        return "https://wa.me/" + digits
    return None


def build_whatsapp(country_code, raw_number):
    """Combine dropdown code + typed number into '+<code> <number>'.
    Returns (value, error). Empty input is fine — the number is optional."""
    raw_number = (raw_number or "").strip()
    if not raw_number:
        return "", None

    digits = re.sub(r"\D", "", raw_number)
    if raw_number.startswith("+"):
        # They typed a full international number; trust it over the dropdown.
        total = digits
        display = "+" + digits
    else:
        if country_code not in VALID_COUNTRY_CODES:
            return "", "Please pick a country code."
        national = digits.lstrip("0")
        total = country_code + national
        display = "+" + country_code + " " + national

    if not (7 <= len(total) <= 15) or not digits:
        return "", "That WhatsApp number doesn't look right — check it or leave it blank."
    return display, None


# ---------------------------------------------------------------------------
# Static portfolio content — edit this to keep every page in sync
# ---------------------------------------------------------------------------

PROFILE = {
    "name": "Daniel Prosper Chinedu",
    "alias": "Sweet",
    "brand": "Consistency",
    "title": "AI Engineering & Prompt Engineering",
    "main_stacks": ["AI Engineering", "Prompt Engineering"],
    "also_good_at": ["Flask", "PostgreSQL", "SQLite3", "Backend"],
    "location": "From Abia State, Nigeria — based in Togo",
    "experience": "1 year of experience building and shipping real products",
    "experience_badge": "1 year of experience",
    "whatsapp_display": "+234 7065457108",
    "whatsapp_digits": "2347065457108",
    "share_text": "Have a look at Daniel (Sweet)'s portfolio: AI engineering & prompt engineering, plus Flask and backend work. 1 year of experience shipping real products.",
    "tagline": "I build, ship, and stay consistent.",
    "intro": (
        "I'm Daniel, known to everyone as Sweet. My main stacks are AI "
        "engineering and prompt engineering. I'm also good at Flask, PostgreSQL, "
        "SQLite3 and backend work, which I build end to end myself, while an "
        "AI I built myself builds my frontends. My flagship project, "
        "Consistency, is a live-streaming platform for tutoring tech students."
    ),
    # Shown on Home, CV and Why Hire Me so visitors hear it from me first.
    "how_i_build": (
        "My frontends are built by an AI I built myself. I direct it with solid "
        "prompts, review what comes back and fix what's wrong. The backend I build "
        "end to end myself, sometimes with a cheat sheet or by reusing code from "
        "my older projects, and I ship and deploy all of it."
    ),
}

PROFILE["whatsapp_url"] = (
    "https://wa.me/" + PROFILE["whatsapp_digits"]
    + "?text=Hi%20Daniel%2C%20I%20found%20your%20portfolio."
)

SKILL_GROUPS = [
    {
        "title": "AI Engineering",
        "main": True,
        "skills": ["LLM Integration (Claude, ChatGPT)", "AI Chatbots & Virtual Assistants",
                   "AI API Integration", "RAG (Retrieval-Augmented Generation)",
                   "Built my own AI that builds my frontends"],
    },
    {
        "title": "Prompt Engineering",
        "main": True,
        "skills": ["Prompt Engineering", "Solid, detailed prompts that direct AI builds",
                   "Reviewing and iterating on AI output"],
    },
    {
        "title": "Backend Development",
        "also": True,
        "skills": ["Python", "Flask", "REST APIs", "Authentication & Authorization",
                   "Backend Architecture", "Backends built end to end by me"],
    },
    {
        "title": "Databases",
        "also": True,
        "skills": ["PostgreSQL", "SQLite3", "Database Deployment & Migration"],
    },
    {
        "title": "Frontend Development (built by my own AI, I direct it)",
        "skills": ["HTML5", "CSS3", "JavaScript", "Responsive Web Design",
                   "Interactive UI Development", "API Integration", "Modern Web Frameworks"],
    },
    {
        "title": "Cybersecurity",
        "skills": ["Web Application Penetration Testing", "Vulnerability Assessment",
                   "OWASP Top 10", "API Security Testing", "Authentication Security",
                   "Security Testing & Debugging"],
    },
    {
        "title": "Deployment & Infrastructure",
        "skills": ["Web Application Deployment", "Render", "Git & GitHub",
                   "Domain & DNS Configuration", "Environment Variables",
                   "Production Configuration"],
    },
    {
        "title": "Project Management & Scaling",
        "skills": ["Agile Project Management", "System Architecture", "Performance Optimization",
                   "Project Scaling", "Debugging & Troubleshooting", "Maintenance & Support"],
    },
]

PROJECTS = [
    {"name": "Consistency", "desc": "Live-streaming platform for tutoring tech students. Tutors go live, students join and chat in real time. Host/viewer roles, Agora video, gift economy, admin panel and PWA support, with the frontend built by my own AI assistant.",
     "url": "https://github.com/Kingcoder-20/Consistency.git"},
    {"name": "Consistency Livestream", "desc": "The live-streaming layer behind Consistency, used for live tech classes between tutors and students.",
     "url": "https://github.com/Kingcoder-20/Consistency-Livestream-.git"},
    {"name": "HwfarApp", "desc": "A communication platform like WhatsApp, for chatting and staying connected.",
     "url": "https://github.com/consistencylivestream-ops/HwfarApp.git"},
    {"name": "VibeMe", "desc": "My personal AI assistant, built for my own day-to-day use.",
     "url": "https://github.com/consistencylivestream-ops/VibeMe.git"},
    {"name": "EziLaws", "desc": "E-commerce platform built for a client, shown here with the client's permission.",
     "url": "https://github.com/consistencylivestream-ops/EziLaws.git"},
    {"name": "TrendForge NIXORA", "desc": "AI tool that generates content ideas.",
     "url": "https://github.com/Kingcoder-20/TrendForge-NIXORA.git"},
    {"name": "Hital", "desc": "A colleague's project that I completed and delivered.",
     "url": "https://github.com/Kingcoder-20/Hital.git"},
]

TRAITS = [
    {"name": "Consistent", "desc": "Shows up and ships, day after day — it's the name of my own product for a reason."},
    {"name": "Hardworking", "desc": "Puts in the hours a real product build actually requires."},
    {"name": "Punctual", "desc": "Respects deadlines and other people's time."},
    {"name": "Zealous", "desc": "Brings real energy and drive to every project."},
    {"name": "Kind", "desc": "Treats people, teammates and users with respect."},
    {"name": "Bold", "desc": "Not afraid to make a decision and own the outcome."},
    {"name": "Clear communicator", "desc": "Explains technical work in plain language to any audience."},
    {"name": "Positive", "desc": "Shows up smiling and keeps a good attitude under pressure."},
    {"name": "Self-aware and improving", "desc": "Openly works on managing frustration under pressure, and it keeps getting better."},
]

REFERENCES = [
    {"role": "Religious Leader", "phone": "+234 905 299 7097", "wa": "2349052997097"},
    {"role": "Mum", "phone": "+228 92 77 80 64", "wa": "22892778064"},
    {"role": "Friend", "phone": "+234 813 998 8264", "wa": "2348139988264"},
    {"role": "Friend", "phone": "+228 71 99 97 73", "wa": "22871999773"},
    {"role": "Brother", "phone": "+228 78 03 71 29", "wa": "22878037129"},
]

WHY_HIRE_ME = [
    {"title": "Upfront about how I build",
     "desc": "AI engineering and prompt engineering are my main stacks. The frontend is built by an AI assistant I built myself, directed by my prompts and specs. The backend (Flask, PostgreSQL, SQLite3) I build end to end myself, sometimes leaning on a cheat sheet or code from my older projects. On both sides, what I bring is the direction and the judgement to review, fix and ship."},
    {"title": "Ships real products",
     "desc": "Consistency is a live-streaming platform for tutoring tech students, with video, payments, real-time chat and admin tooling. I write the backend, direct the AI-built frontend and deploy it myself."},
    {"title": "Comfortable in production, not just tutorials",
     "desc": "Has debugged real production failures: DNS resolution issues in email delivery, silent JavaScript crashes, deployment failures on Render — and fixed them."},
    {"title": "Builds for constraints, not ideal conditions",
     "desc": "Develops full production web apps from an Android phone using Pydroid 3. That's proof of resourcefulness that a comfortable dev setup doesn't test."},
    {"title": "Understands the business side of the product",
     "desc": "Designed a working gift economy — coins, gifts, diamonds, cash withdrawal, revenue share — not just the code behind it, but the mechanic itself."},
    {"title": "Security-conscious by habit",
     "desc": "Applies OWASP Top 10 thinking, authentication security, and API security testing as part of the normal build process, not an afterthought."},
    {"title": "Learns fast and integrates modern tooling",
     "desc": "Already building with LLM integration, prompt engineering and RAG — comfortable putting AI to work inside real products."},
]


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html", profile=PROFILE, traits=TRAITS[:4], project_count=len(PROJECTS))


@app.route("/skills")
def skills():
    return render_template("skills.html", profile=PROFILE, groups=SKILL_GROUPS)


@app.route("/projects")
def projects():
    return render_template("projects.html", profile=PROFILE, projects=PROJECTS)


@app.route("/cv")
def cv():
    return render_template("cv.html", profile=PROFILE, groups=SKILL_GROUPS, projects=PROJECTS)


@app.route("/why-hire-me")
def why_hire_me():
    return render_template("why.html", profile=PROFILE, reasons=WHY_HIRE_ME)


@app.route("/traits")
def traits():
    return render_template("traits.html", profile=PROFILE, traits=TRAITS, references=REFERENCES)


@app.route("/contact", methods=["GET", "POST"])
def contact():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        message = request.form.get("message", "").strip()
        if not name or not message:
            return jsonify({"ok": False, "error": "Please add your name and a comment."}), 400
        if len(name) > MAX_NAME_LEN or len(message) > MAX_MESSAGE_LEN:
            return jsonify({"ok": False,
                            "error": f"Please keep your name under {MAX_NAME_LEN} and your comment under {MAX_MESSAGE_LEN} characters."}), 400
        if rate_limited("comment", 8, 600):
            return jsonify({"ok": False, "error": "Too many comments in a short time. Please try again in a few minutes."}), 429

        # WhatsApp number is optional.
        whatsapp, error = build_whatsapp(
            request.form.get("country_code", "").strip(),
            request.form.get("phone", ""),
        )
        if error:
            return jsonify({"ok": False, "error": error}), 400

        db.execute(
            "INSERT INTO comments (name, whatsapp, message, created_at) VALUES (?, ?, ?, ?)",
            (name, whatsapp, message, datetime.utcnow().isoformat()),
        )
        db.commit()
        return jsonify({"ok": True})

    rows = db.execute(
        "SELECT name, whatsapp, message, created_at FROM comments ORDER BY id DESC"
    ).fetchall()
    comments = []
    for r in rows:
        c = dict(r)
        c["wa_url"] = wa_link(c["whatsapp"])
        comments.append(c)
    return render_template(
        "contact.html",
        profile=PROFILE,
        comments=comments,
        common_countries=COMMON_COUNTRIES,
        other_countries=OTHER_COUNTRIES,
        default_code=DEFAULT_COUNTRY_CODE,
    )


@app.route("/live")
def live():
    return render_template("live.html", profile=PROFILE)


# ---------------------------------------------------------------------------
# PWA: manifest + service worker (served from the site root so scope is "/")
# ---------------------------------------------------------------------------

# manifest.json, sw.js and the app icons live in the project root (next to
# app.py), not in /static. Only the exact files below are ever served from
# the root, so app.py and the rest of the source stay private.
ICON_SIZES = {"192x192", "512x512", "maskable-192x192", "maskable-512x512"}


@app.route("/manifest.json")
def manifest():
    resp = send_from_directory(BASE_DIR, "manifest.json", mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(BASE_DIR, "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/icon-<size>.png")
def icon(size):
    if size not in ICON_SIZES:
        abort(404)
    resp = send_from_directory(BASE_DIR, f"icon-{size}.png", mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# ---------------------------------------------------------------------------
# Live-visitor API
# ---------------------------------------------------------------------------

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """Called by every page on load and every few seconds after, so the
    server always knows who is currently on the site and for how long."""
    data = request.get_json(silent=True) or {}
    if rate_limited("heartbeat", 120, 60):
        return jsonify({"error": "slow down"}), 429
    visitor_id = data.get("visitor_id")
    page = data.get("page", "/")
    if not isinstance(visitor_id, str) or not VISITOR_ID_RE.match(visitor_id):
        visitor_id = str(uuid.uuid4())
    page = page[:MAX_PAGE_LEN] if isinstance(page, str) else "/"

    now = time.time()
    db = get_db()
    row = db.execute(
        "SELECT first_seen FROM visitors WHERE visitor_id = ?", (visitor_id,)
    ).fetchone()

    if row is None:
        db.execute(
            "INSERT INTO visitors (visitor_id, first_seen, last_seen, page) VALUES (?, ?, ?, ?)",
            (visitor_id, now, now, page),
        )
        first_seen = now
    else:
        db.execute(
            "UPDATE visitors SET last_seen = ?, page = ? WHERE visitor_id = ?",
            (now, page, visitor_id),
        )
        first_seen = row["first_seen"]

    db.commit()
    return jsonify({"visitor_id": visitor_id, "duration_seconds": int(now - first_seen)})


@app.route("/api/live-visitors")
def live_visitors():
    now = time.time()
    cutoff = now - LIVE_WINDOW_SECONDS
    db = get_db()

    # Housekeeping: drop visitors nobody has heard from in over an hour.
    db.execute("DELETE FROM visitors WHERE last_seen < ?", (now - 3600,))
    db.commit()

    rows = db.execute(
        "SELECT visitor_id, first_seen, page FROM visitors WHERE last_seen >= ? ORDER BY first_seen ASC",
        (cutoff,),
    ).fetchall()

    visitors = []
    for r in rows:
        duration = int(now - r["first_seen"])
        visitors.append({
            "id": r["visitor_id"][:8],
            "page": r["page"],
            "duration_seconds": duration,
        })

    return jsonify({"count": len(visitors), "visitors": visitors})


# ---------------------------------------------------------------------------
# Health check + error pages
# ---------------------------------------------------------------------------

@app.route("/healthz")
def healthz():
    """Used by Docker / the host to know the app and its database are alive."""
    try:
        get_db().execute("SELECT 1").fetchone()
    except sqlite3.Error:
        log.exception("health check failed")
        return jsonify({"status": "error"}), 503
    return jsonify({"status": "ok"})


def _error_response(code, title, text):
    if request.path.startswith("/api/"):
        return jsonify({"error": text}), code
    page = (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title} — MyPortfolio</title>"
        "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0a08;"
        "color:#f3ead2;font-family:system-ui,sans-serif;text-align:center;padding:24px}"
        "h1{color:#e8c766;margin:0 0 8px}a{color:#e8c766}</style></head><body><main>"
        f"<h1>{code}</h1><p>{text}</p><p><a href='/'>Back to MyPortfolio</a></p></main></body></html>"
    )
    return page, code


@app.errorhandler(404)
def not_found(_e):
    return _error_response(404, "Page not found", "That page doesn't exist.")


@app.errorhandler(413)
def too_large(_e):
    return _error_response(413, "Too large", "That request was too large.")


@app.errorhandler(500)
def server_error(_e):
    return _error_response(500, "Server error", "Something went wrong on our side. Please try again.")


if __name__ == "__main__":
    # Local development only. In production the app is started by gunicorn
    # (see Dockerfile / gunicorn.conf.py), never with debug mode on.
    if DEBUG:
        log.warning("Flask debug mode is ON - never use this on a public server.")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
