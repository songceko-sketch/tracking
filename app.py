"""
NexaTrackers - Shipment Tracking Application
PostgreSQL version for Vercel deployment
"""
import os
import uuid
import math
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps

import psycopg2
import psycopg2.extras
import cloudinary
import cloudinary.uploader
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, Response)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "logistics-royal-dev-secret")

UPLOAD_FOLDER = "/tmp" if os.environ.get("DATABASE_URL") else os.path.join("static", "uploads")
ALLOWED_EXT   = {"png", "jpg", "jpeg", "webp", "gif"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except Exception:
    pass

# ── Cloudinary config ─────────────────────────────────────────────────────────
cloudinary.config(
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "xzobgefm"),
    api_key    = os.environ.get("CLOUDINARY_API_KEY", "216318784932428"),
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "ERqXN3dM6v1urUrbn7Grhsuj5VM"),
    secure     = True
)

# ── Email config ──────────────────────────────────────────────────────────────
try:
    import email_config
except ImportError:
    class email_config:
        MAIL_ENABLED  = os.environ.get("MAIL_ENABLED", "false").lower() == "true"
        MAIL_SENDER   = os.environ.get("MAIL_SENDER", "")
        MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
        MAIL_SMTP     = os.environ.get("MAIL_SMTP", "smtp.hostinger.com")
        MAIL_PORT     = int(os.environ.get("MAIL_PORT", "465"))


# ── DB helpers ────────────────────────────────────────────────────────────────
def get_db():
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        return psycopg2.connect(
            db_url,
            cursor_factory=psycopg2.extras.RealDictCursor
        )
    # ── Local fallback: SQLite ──
    import sqlite3
    conn = sqlite3.connect("shipments.db")
    conn.row_factory = sqlite3.Row
    return conn

def query(sql, args=(), one=False, commit=False):
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        sql = sql.replace("?", "%s")
    conn = get_db()
    cur  = conn.cursor()
    cur.execute(sql, args)
    if commit:
        conn.commit()
        rowid = None
        if db_url:
            try:
                cur.execute("SELECT lastval()")
                row = cur.fetchone()
                rowid = row["lastval"] if isinstance(row, dict) else row[0]
            except Exception:
                pass
        else:
            rowid = cur.lastrowid
        conn.close()
        return rowid
    rv = cur.fetchone() if one else cur.fetchall()
    conn.close()
    if rv is None:
        return None
    if one:
        return dict(rv)
    return [dict(r) for r in rv]

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def _first_form_value(form_data, *field_names):
    for field_name in field_names:
        values = form_data.getlist(field_name)
        for value in values:
            clean = (value or "").strip()
            if clean:
                return clean
    return ""


def _first_form_float(form_data, *field_names, default=0):
    value = _first_form_value(form_data, *field_names)
    if not value:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_table_columns(table_name):
    conn = get_db()
    try:
        cur = conn.cursor()
        db_url = os.environ.get("DATABASE_URL", "")
        if db_url:
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s
                ORDER BY ordinal_position
            """, (table_name,))
            return [row["column_name"] for row in cur.fetchall()]
        cur.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cur.fetchall()]
    finally:
        conn.close()


def ensure_shipment_columns():
    columns = get_table_columns("shipments")
    if not columns:
        init_db()
        columns = get_table_columns("shipments")
    if not columns:
        raise RuntimeError("shipments table was not created in the configured database")

    db_url = os.environ.get("DATABASE_URL", "")
    required = [
        ("sender_name", "TEXT DEFAULT ''"),
        ("sender_email", "TEXT DEFAULT ''"),
        ("sender_contact", "TEXT DEFAULT ''"),
        ("sender_country", "TEXT DEFAULT ''"),
        ("sender_freight_type", "TEXT DEFAULT ''"),
        ("sender_date", "TEXT DEFAULT ''"),
        ("sender_time", "TEXT DEFAULT ''"),
        ("sender_address", "TEXT DEFAULT ''"),
        ("sender_pickup_date", "TEXT DEFAULT ''"),
        ("sender_pickup_time", "TEXT DEFAULT ''"),
        ("receiver_name", "TEXT DEFAULT ''"),
        ("receiver_email", "TEXT DEFAULT ''"),
        ("receiver_contact", "TEXT DEFAULT ''"),
        ("receiver_country", "TEXT DEFAULT ''"),
        ("receiver_freight_type", "TEXT DEFAULT ''"),
        ("receiver_date", "TEXT DEFAULT ''"),
        ("receiver_time", "TEXT DEFAULT ''"),
        ("receiver_address", "TEXT DEFAULT ''"),
        ("shipment_description", "TEXT DEFAULT ''"),
        ("current_location", "TEXT DEFAULT ''"),
        ("origin", "TEXT DEFAULT ''"),
        ("destination", "TEXT DEFAULT ''"),
        ("departure_date", "TEXT DEFAULT ''"),
        ("departure_time", "TEXT DEFAULT ''"),
        ("arrival_date", "TEXT DEFAULT ''"),
        ("arrival_time", "TEXT DEFAULT ''"),
        ("expected_delivery_date", "TEXT DEFAULT ''"),
        ("expected_delivery_time", "TEXT DEFAULT ''"),
        ("comments", "TEXT DEFAULT ''"),
    ]

    conn = get_db()
    cur = conn.cursor()
    try:
        for col_name, col_type in required:
            if col_name not in columns:
                cur.execute(f"ALTER TABLE shipments ADD COLUMN {col_name} {col_type}")
        conn.commit()
    finally:
        conn.close()


def insert_shipment_record(payload):
    columns = get_table_columns("shipments")
    if not columns:
        init_db()
        ensure_shipment_columns()
        columns = get_table_columns("shipments")
    if not columns:
        raise RuntimeError("shipments table is missing from the configured database")

    valid_data = {key: value for key, value in payload.items() if key in columns and value is not None}
    if not valid_data:
        raise RuntimeError("no valid shipment fields to insert")

    ordered_columns = [col for col in columns if col in valid_data]
    if not ordered_columns:
        raise RuntimeError("shipment payload does not match table columns")

    placeholders = ", ".join(["?"] * len(ordered_columns))
    sql = f"INSERT INTO shipments ({', '.join(ordered_columns)}) VALUES ({placeholders})"
    values = [valid_data[col] for col in ordered_columns]
    query(sql, values, commit=True)
    return True


# ── DB init (runs on every cold start, safe due to IF NOT EXISTS) ─────────────
def init_db():
    db_url = os.environ.get("DATABASE_URL", "")
    conn = get_db()
    cur  = conn.cursor()

    if db_url:
        # PostgreSQL
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                username      TEXT   NOT NULL UNIQUE,
                password_hash TEXT   NOT NULL,
                email         TEXT   NOT NULL,
                role          TEXT   NOT NULL CHECK(role IN ('admin','client'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                id                  SERIAL PRIMARY KEY,
                tracking_number     TEXT   NOT NULL UNIQUE,
                client_id           INT    NOT NULL REFERENCES users(id),
                current_status      TEXT   NOT NULL DEFAULT 'Pending',
                destination_address TEXT   NOT NULL DEFAULT '',
                sender_name         TEXT   DEFAULT '',
                sender_email        TEXT   DEFAULT '',
                sender_contact      TEXT   DEFAULT '',
                sender_country      TEXT   DEFAULT '',
                sender_freight_type TEXT   DEFAULT '',
                sender_date         TEXT   DEFAULT '',
                sender_time         TEXT   DEFAULT '',
                sender_address      TEXT   DEFAULT '',
                sender_pickup_date  TEXT   DEFAULT '',
                sender_pickup_time  TEXT   DEFAULT '',
                receiver_name       TEXT   DEFAULT '',
                receiver_email      TEXT   DEFAULT '',
                receiver_contact    TEXT   DEFAULT '',
                receiver_country    TEXT   DEFAULT '',
                receiver_freight_type TEXT  DEFAULT '',
                receiver_date       TEXT   DEFAULT '',
                receiver_time       TEXT   DEFAULT '',
                receiver_address    TEXT   DEFAULT '',
                shipment_description TEXT  DEFAULT '',
                current_location    TEXT   DEFAULT '',
                origin             TEXT   DEFAULT '',
                destination        TEXT   DEFAULT '',
                departure_date     TEXT   DEFAULT '',
                departure_time     TEXT   DEFAULT '',
                arrival_date       TEXT   DEFAULT '',
                arrival_time       TEXT   DEFAULT '',
                expected_delivery_date TEXT DEFAULT '',
                expected_delivery_time TEXT DEFAULT '',
                comments           TEXT   DEFAULT '',
                weight_kg           REAL   DEFAULT 0,
                height_cm           REAL   DEFAULT 0,
                width_cm            REAL   DEFAULT 0,
                length_cm           REAL   DEFAULT 0,
                description         TEXT   DEFAULT '',
                package_type        TEXT   DEFAULT '',
                image_filename      TEXT   DEFAULT '',
                created_at          TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shipment_history (
                id           SERIAL PRIMARY KEY,
                shipment_id  INT  NOT NULL REFERENCES shipments(id),
                city_name    TEXT NOT NULL,
                status_notes TEXT NOT NULL,
                updated_at   TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("SELECT id FROM users WHERE username = 'admin'")
        if not cur.fetchone():
            admin_pass = os.environ.get("ADMIN_PASSWORD", "admin123")
            cur.execute(
                "INSERT INTO users (username, password_hash, email, role) VALUES (%s, %s, %s, 'admin')",
                ("admin", generate_password_hash(admin_pass), "admin@logisticsroyal.com")
            )
    else:
        # SQLite
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                email         TEXT    NOT NULL,
                role          TEXT    NOT NULL CHECK(role IN ('admin','client'))
            );
            CREATE TABLE IF NOT EXISTS shipments (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking_number       TEXT    NOT NULL UNIQUE,
                client_id             INTEGER NOT NULL REFERENCES users(id),
                current_status        TEXT    NOT NULL DEFAULT 'Pending',
                destination_address   TEXT    NOT NULL DEFAULT '',
                sender_name           TEXT    DEFAULT '',
                sender_email          TEXT    DEFAULT '',
                sender_contact        TEXT    DEFAULT '',
                sender_country        TEXT    DEFAULT '',
                sender_freight_type   TEXT    DEFAULT '',
                sender_date           TEXT    DEFAULT '',
                sender_time           TEXT    DEFAULT '',
                sender_address        TEXT    DEFAULT '',
                sender_pickup_date    TEXT    DEFAULT '',
                sender_pickup_time    TEXT    DEFAULT '',
                receiver_name         TEXT    DEFAULT '',
                receiver_email        TEXT    DEFAULT '',
                receiver_contact      TEXT    DEFAULT '',
                receiver_country      TEXT    DEFAULT '',
                receiver_freight_type TEXT    DEFAULT '',
                receiver_date         TEXT    DEFAULT '',
                receiver_time         TEXT    DEFAULT '',
                receiver_address      TEXT    DEFAULT '',
                shipment_description  TEXT    DEFAULT '',
                current_location      TEXT    DEFAULT '',
                origin               TEXT    DEFAULT '',
                destination           TEXT    DEFAULT '',
                departure_date       TEXT    DEFAULT '',
                departure_time       TEXT    DEFAULT '',
                arrival_date         TEXT    DEFAULT '',
                arrival_time         TEXT    DEFAULT '',
                expected_delivery_date TEXT   DEFAULT '',
                expected_delivery_time TEXT   DEFAULT '',
                comments             TEXT    DEFAULT '',
                weight_kg             REAL    DEFAULT 0,
                height_cm             REAL    DEFAULT 0,
                width_cm              REAL    DEFAULT 0,
                length_cm             REAL    DEFAULT 0,
                description           TEXT    DEFAULT '',
                package_type          TEXT    DEFAULT '',
                image_filename        TEXT    DEFAULT '',
                created_at            DATETIME DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS shipment_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id  INTEGER NOT NULL REFERENCES shipments(id),
                city_name    TEXT    NOT NULL,
                status_notes TEXT    NOT NULL,
                updated_at   DATETIME DEFAULT (datetime('now'))
            );
        """)
        cur.execute("SELECT id FROM users WHERE username = 'admin'")
        if not cur.fetchone():
            admin_pass = os.environ.get("ADMIN_PASSWORD", "admin123")
            cur.execute(
                "INSERT INTO users (username, password_hash, email, role) VALUES (?, ?, ?, 'admin')",
                ("admin", generate_password_hash(admin_pass), "admin@logisticsroyal.com")
            )

    conn.commit()
    conn.close()

try:
    init_db()
    ensure_shipment_columns()
except Exception as e:
    print(f"[DB INIT ERROR] {e}")


# ── Auth decorator ────────────────────────────────────────────────────────────
def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return wrapped
    return decorator



@app.route("/")
def home():
    return render_template("home.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    sent = False
    if request.method == "POST":
        name    = request.form.get("name", "").strip()
        email   = request.form.get("email", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if name and email and message:
            def _send():
                try:
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = f"[NexaTrackers Contact] {subject or 'New Enquiry'}"
                    msg["From"]    = email_config.MAIL_SENDER
                    msg["To"]      = "info@nexatrackers.com"
                    html = f"""
                    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto">
                      <div style="background:#1e3a5f;padding:24px 32px">
                        <h1 style="color:#fff;margin:0;font-size:20px">&#128231; New Contact Form — NexaTrackers</h1>
                      </div>
                      <div style="padding:28px;font-size:14px;color:#334155">
                        <p><strong>Name:</strong> {name}</p>
                        <p><strong>Email:</strong> {email}</p>
                        <p><strong>Subject:</strong> {subject or 'N/A'}</p>
                        <p><strong>Message:</strong></p>
                        <div style="background:#f1f5f9;padding:16px;border-radius:8px">{message}</div>
                      </div>
                    </div>
                    """
                    msg.attach(MIMEText(html, "html"))
                    with smtplib.SMTP_SSL(email_config.MAIL_SMTP, email_config.MAIL_PORT) as s:
                        s.login(email_config.MAIL_SENDER, email_config.MAIL_PASSWORD)
                        s.sendmail(email_config.MAIL_SENDER, email_config.MAIL_SENDER, msg.as_string())
                except Exception as e:
                    print(f"[CONTACT EMAIL ERROR] {e}")
            if email_config.MAIL_ENABLED:
                threading.Thread(target=_send, daemon=True).start()
            sent = True
    return render_template("contact.html", sent=sent)

@app.route("/track", methods=["GET", "POST"])
def public_track():
    result, history, error, tracking_number = None, [], None, ""
    requested_number = request.form.get("tracking_number", "").strip().upper() if request.method == "POST" else request.args.get("tracking_number", "").strip().upper()
    if requested_number:
        tracking_number = requested_number
        shipment = query("SELECT * FROM shipments WHERE tracking_number = ?",
                         (tracking_number,), one=True)
        if shipment:
            history = query(
                "SELECT * FROM shipment_history WHERE shipment_id = ? ORDER BY updated_at ASC",
                (shipment["id"],)
            )
            result = shipment
        else:
            error = f'No shipment found for "{tracking_number}". Please check and try again.'
    elif request.method == "POST":
        error = 'Please enter a tracking number.'
    return render_template("public_track.html",
                           result=result, history=history,
                           error=error, tracking_number=tracking_number)


# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("admin_dashboard") if session["role"] == "admin"
                        else url_for("client_dashboard"))
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = query("SELECT * FROM users WHERE username = ?", (username,), one=True)
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("admin_dashboard") if user["role"] == "admin"
                            else url_for("client_dashboard"))
        flash("Invalid username or password.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ── Admin ─────────────────────────────────────────────────────────────────────
@app.route("/admin")
@login_required(role="admin")
def admin_dashboard():
    clients = query("SELECT id, username, email FROM users WHERE role = 'client'")
    shipments = query("""
        SELECT s.id, s.tracking_number, s.current_status, s.created_at,
               s.destination_address, s.weight_kg, s.height_cm, s.width_cm,
               s.length_cm, s.description, s.package_type, s.image_filename,
               u.username
        FROM shipments s JOIN users u ON s.client_id = u.id
        ORDER BY s.created_at DESC
    """)
    return render_template("admin.html", clients=clients, shipments=shipments)

@app.route("/admin/create_client", methods=["POST"])
@login_required(role="admin")
def create_client():
    username = request.form["username"].strip()
    email    = request.form["email"].strip()
    password = request.form["password"]
    if query("SELECT id FROM users WHERE username = ?", (username,), one=True):
        flash("Username already exists.")
        return redirect(url_for("admin_dashboard"))
    query(
        "INSERT INTO users (username, password_hash, email, role) VALUES (?, ?, ?, 'client')",
        (username, generate_password_hash(password), email),
        commit=True
    )
    flash(f"Client '{username}' created successfully.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/delete_client/<int:client_id>", methods=["POST"])
@login_required(role="admin")
def delete_client(client_id):
    client = query(
        "SELECT id, username FROM users WHERE id = ? AND role = 'client'",
        (client_id,),
        one=True
    )
    if not client:
        flash("Client not found.")
        return redirect(url_for("admin_dashboard"))

    shipment_ids = [
        row["id"] for row in query(
            "SELECT id FROM shipments WHERE client_id = ?",
            (client_id,)
        )
    ]

    for shipment_id in shipment_ids:
        query(
            "DELETE FROM shipment_history WHERE shipment_id = ?",
            (shipment_id,),
            commit=True
        )

    query("DELETE FROM shipments WHERE client_id = ?", (client_id,), commit=True)
    query("DELETE FROM users WHERE id = ? AND role = 'client'", (client_id,), commit=True)

    flash(f"Client '{client['username']}' deleted successfully.")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/create_shipment", methods=["POST"])
@login_required(role="admin")
def create_shipment():
    client_id_raw = _first_form_value(request.form, "client_id")
    if not client_id_raw:
        flash("Please select a valid client before creating a shipment.")
        return redirect(url_for("admin_dashboard"))
    try:
        client_id = int(client_id_raw)
    except (TypeError, ValueError):
        flash("The selected client is invalid. Please choose a client from the list.")
        return redirect(url_for("admin_dashboard"))

    destination_address = _first_form_value(request.form, "destination", "receiver_address", "destination_address")
    description = _first_form_value(request.form, "shipment_description", "description")
    package_type = _first_form_value(request.form, "package_type")
    weight_kg = _first_form_float(request.form, "weight_kg", default=0)
    height_cm = _first_form_float(request.form, "height_cm", default=0)
    width_cm = _first_form_float(request.form, "width_cm", default=0)
    length_cm = _first_form_float(request.form, "length_cm", default=0)

    sender_name = _first_form_value(request.form, "sender_name")
    sender_email = _first_form_value(request.form, "sender_email")
    sender_contact = _first_form_value(request.form, "sender_contact")
    sender_country = _first_form_value(request.form, "sender_country", "origin")
    sender_freight_type = _first_form_value(request.form, "sender_freight_type")
    sender_date = _first_form_value(request.form, "sender_date", "departure_date")
    sender_time = _first_form_value(request.form, "sender_time", "departure_time")
    sender_address = _first_form_value(request.form, "sender_address", "current_location")
    sender_pickup_date = _first_form_value(request.form, "sender_pickup_date")
    sender_pickup_time = _first_form_value(request.form, "sender_pickup_time")

    receiver_name = _first_form_value(request.form, "receiver_name")
    receiver_email = _first_form_value(request.form, "receiver_email")
    receiver_contact = _first_form_value(request.form, "receiver_contact")
    receiver_country = _first_form_value(request.form, "receiver_country", "destination")
    receiver_freight_type = _first_form_value(request.form, "receiver_freight_type")
    receiver_date = _first_form_value(request.form, "receiver_date", "arrival_date", "expected_delivery_date")
    receiver_time = _first_form_value(request.form, "receiver_time", "arrival_time", "expected_delivery_time")
    receiver_address = _first_form_value(request.form, "receiver_address", "destination")

    current_status = _first_form_value(request.form, "status") or "Registered & Pending"
    current_location = _first_form_value(request.form, "current_location") or sender_address
    origin = _first_form_value(request.form, "origin") or sender_country
    destination = _first_form_value(request.form, "destination") or receiver_address or receiver_country
    departure_date = _first_form_value(request.form, "departure_date")
    departure_time = _first_form_value(request.form, "departure_time")
    arrival_date = _first_form_value(request.form, "arrival_date")
    arrival_time = _first_form_value(request.form, "arrival_time")
    expected_delivery_date = _first_form_value(request.form, "expected_delivery_date")
    expected_delivery_time = _first_form_value(request.form, "expected_delivery_time")
    comments = _first_form_value(request.form, "comments")

    tracking_number = "TRK-" + uuid.uuid4().hex[:8].upper()

    image_filename = ""
    file = request.files.get("package_image")
    if file and file.filename and allowed_file(file.filename):
        try:
            result = cloudinary.uploader.upload(file, folder="nexatrack", public_id=tracking_number)
            image_filename = result.get("secure_url", "")
        except Exception as e:
            print(f"[CLOUDINARY ERROR] {e}")
            image_filename = ""

    payload = {
        "tracking_number": tracking_number,
        "client_id": client_id,
        "current_status": current_status,
        "destination_address": destination_address,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "sender_contact": sender_contact,
        "sender_country": sender_country,
        "sender_freight_type": sender_freight_type,
        "sender_date": sender_date,
        "sender_time": sender_time,
        "sender_address": sender_address,
        "sender_pickup_date": sender_pickup_date,
        "sender_pickup_time": sender_pickup_time,
        "receiver_name": receiver_name,
        "receiver_email": receiver_email,
        "receiver_contact": receiver_contact,
        "receiver_country": receiver_country,
        "receiver_freight_type": receiver_freight_type,
        "receiver_date": receiver_date,
        "receiver_time": receiver_time,
        "receiver_address": receiver_address,
        "shipment_description": description,
        "current_location": current_location,
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "departure_time": departure_time,
        "arrival_date": arrival_date,
        "arrival_time": arrival_time,
        "expected_delivery_date": expected_delivery_date,
        "expected_delivery_time": expected_delivery_time,
        "comments": comments,
        "weight_kg": weight_kg,
        "height_cm": height_cm,
        "width_cm": width_cm,
        "length_cm": length_cm,
        "description": description,
        "package_type": package_type,
        "image_filename": image_filename,
    }

    try:
        insert_shipment_record(payload)
        flash(f"Shipment {tracking_number} created and assigned.")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[CREATE_SHIPMENT_ERROR] {exc}")
        flash(f"Shipment creation failed: {exc}")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/delete_shipment/<int:shipment_id>", methods=["POST"])
@login_required(role="admin")
def delete_shipment(shipment_id):
    shipment = query(
        "SELECT tracking_number FROM shipments WHERE id = ?",
        (shipment_id,), one=True
    )
    if not shipment:
        flash("Shipment not found.")
        return redirect(url_for("admin_dashboard"))

    query("DELETE FROM shipment_history WHERE shipment_id = ?", (shipment_id,), commit=True)
    query("DELETE FROM shipments WHERE id = ?", (shipment_id,), commit=True)
    flash(f"Shipment {shipment['tracking_number']} deleted.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/edit_shipment/<int:shipment_id>", methods=["GET", "POST"])
@login_required(role="admin")
def edit_shipment(shipment_id):
    shipment = query("SELECT * FROM shipments WHERE id = ?", (shipment_id,), one=True)
    if not shipment:
        flash("Shipment not found.")
        return redirect(url_for("admin_dashboard"))

    editable_text_fields = (
        "current_status", "current_location", "origin", "destination",
        "destination_address", "sender_name", "sender_email", "sender_contact",
        "sender_country", "sender_freight_type", "sender_date", "sender_time",
        "sender_address", "sender_pickup_date", "sender_pickup_time",
        "receiver_name", "receiver_email", "receiver_contact", "receiver_country",
        "receiver_freight_type", "receiver_date", "receiver_time", "receiver_address",
        "shipment_description", "description", "package_type", "departure_date",
        "departure_time", "arrival_date", "arrival_time", "expected_delivery_date",
        "expected_delivery_time", "comments",
    )
    editable_numeric_fields = ("weight_kg", "height_cm", "width_cm", "length_cm")

    if request.method == "POST":
        available_columns = set(get_table_columns("shipments"))
        updates = {
            field: request.form.get(field, "").strip()
            for field in editable_text_fields
            if field in available_columns
        }
        if "current_status" in updates and not updates["current_status"]:
            flash("Shipment status cannot be empty.")
            return redirect(url_for("edit_shipment", shipment_id=shipment_id))
        try:
            for field in editable_numeric_fields:
                if field in available_columns:
                    raw_value = request.form.get(field, "").strip()
                    numeric_value = float(raw_value) if raw_value else 0
                    if not math.isfinite(numeric_value) or numeric_value < 0:
                        raise ValueError
                    updates[field] = numeric_value
        except ValueError:
            flash("Weight and dimensions must be valid numbers.")
            return redirect(url_for("edit_shipment", shipment_id=shipment_id))

        if not updates:
            flash("No editable shipment fields are available in the database.")
            return redirect(url_for("edit_shipment", shipment_id=shipment_id))

        location_changed = updates.get("current_location", shipment.get("current_location", "")) != (shipment.get("current_location") or "")
        status_changed = updates.get("current_status", shipment.get("current_status", "")) != (shipment.get("current_status") or "")
        update_columns = list(updates)
        assignments = ", ".join(f"{column} = ?" for column in update_columns)
        query(
            f"UPDATE shipments SET {assignments} WHERE id = ?",
            [updates[column] for column in update_columns] + [shipment_id],
            commit=True,
        )

        if location_changed or status_changed:
            current_location = updates.get("current_location") or shipment.get("current_location") or updates.get("origin") or "Shipment update"
            current_status = updates.get("current_status") or shipment.get("current_status") or "Shipment updated"
            query(
                "INSERT INTO shipment_history (shipment_id, city_name, status_notes) VALUES (?, ?, ?)",
                (shipment_id, current_location, current_status),
                commit=True,
            )
            recipient = query("""
                SELECT u.email, u.username, s.tracking_number
                FROM shipments s JOIN users u ON s.client_id = u.id WHERE s.id = ?
            """, (shipment_id,), one=True)
            if recipient:
                _send_update_email(
                    recipient["email"], recipient["username"], recipient["tracking_number"],
                    current_location, current_status
                )

        flash(f"Shipment {shipment['tracking_number']} updated successfully.")
        return redirect(url_for("edit_shipment", shipment_id=shipment_id))

    return render_template("shipment_edit.html", shipment=shipment)


@app.route("/admin/update_location", methods=["POST"])
@login_required(role="admin")
def update_location():
    shipment_id  = request.form["shipment_id"]
    city_name    = request.form["city_name"].strip()
    status_notes = request.form["status_notes"].strip()
    query(
        "INSERT INTO shipment_history (shipment_id, city_name, status_notes) VALUES (?, ?, ?)",
        (shipment_id, city_name, status_notes), commit=True
    )
    query("UPDATE shipments SET current_status = ? WHERE id = ?",
          (status_notes, shipment_id), commit=True)
    row = query("""
        SELECT u.email, u.username, s.tracking_number
        FROM shipments s JOIN users u ON s.client_id = u.id WHERE s.id = ?
    """, (shipment_id,), one=True)
    if row:
        _send_update_email(row["email"], row["username"],
                           row["tracking_number"], city_name, status_notes)
    flash("Location updated and client notified by email.")
    return redirect(url_for("admin_dashboard"))


# ── Client ────────────────────────────────────────────────────────────────────
@app.route("/client")
@login_required(role="client")
def client_dashboard():
    shipments = query(
        "SELECT * FROM shipments WHERE client_id = ? ORDER BY created_at DESC",
        (session["user_id"],)
    )
    return render_template("client.html", shipments=shipments)

@app.route("/client/track/<int:shipment_id>")
@login_required(role="client")
def track_shipment(shipment_id):
    shipment = query(
        "SELECT * FROM shipments WHERE id = ? AND client_id = ?",
        (shipment_id, session["user_id"]), one=True
    )
    if not shipment:
        flash("Shipment not found.")
        return redirect(url_for("client_dashboard"))
    shipment_history = query(
        "SELECT * FROM shipment_history WHERE shipment_id = ? ORDER BY updated_at ASC",
        (shipment_id,)
    )
    return render_template("tracking.html", shipment=shipment,
                           shipment_history=shipment_history)


# ── Email helper ──────────────────────────────────────────────────────────────
def _send_update_email(to_email, username, tracking_number, city, status):
    if not email_config.MAIL_ENABLED:
        return
    def _send():
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"NexaTrackers Update: {tracking_number} — {status}"
            msg["From"]    = email_config.MAIL_SENDER
            msg["To"]      = to_email
            html = f"""
            <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;
                        border:1px solid #e2e8f0;border-radius:12px;overflow:hidden">
              <div style="background:#1e3a5f;padding:24px 32px">
                <h1 style="color:#fff;margin:0;font-size:22px">&#128230; NexaTrackers</h1>
                <p style="color:#93c5fd;margin:6px 0 0;font-size:13px">Shipment Status Update</p>
              </div>
              <div style="padding:32px">
                <p style="color:#334155;font-size:15px">Hi <strong>{username}</strong>,</p>
                <div style="background:#f1f5f9;border-radius:10px;padding:20px;margin:20px 0">
                  <table style="width:100%;font-size:14px;color:#334155">
                    <tr><td style="padding:6px 0;color:#64748b">Tracking Number</td>
                        <td style="font-weight:700;font-family:monospace">{tracking_number}</td></tr>
                    <tr><td style="padding:6px 0;color:#64748b">Current Location</td>
                        <td style="font-weight:600">&#128205; {city}</td></tr>
                    <tr><td style="padding:6px 0;color:#64748b">Status</td>
                        <td><span style="background:#dbeafe;color:#1d4ed8;padding:3px 10px;
                                         border-radius:20px;font-size:12px;font-weight:600">{status}</span></td></tr>
                  </table>
                </div>
                <a href="{os.environ.get('APP_URL','https://your-app.vercel.app')}/track"
                   style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;
                          padding:12px 28px;border-radius:8px;font-weight:600;font-size:14px">
                  View Live Tracking &#8594;
                </a>
              </div>
            </div>
            """
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP_SSL(email_config.MAIL_SMTP, email_config.MAIL_PORT) as s:
                s.login(email_config.MAIL_SENDER, email_config.MAIL_PASSWORD)
                s.sendmail(email_config.MAIL_SENDER, to_email, msg.as_string())
        except Exception as e:
            print(f"[EMAIL ERROR] {e}")
    threading.Thread(target=_send, daemon=True).start()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
