from pathlib import Path
import argparse
import base64
import datetime
import hashlib
import json
import os
import random
import re
import secrets
import sqlite3
import threading
import time

from flask import Flask, jsonify, make_response, request, send_from_directory

from src.factory_twin import FactoryDigitalTwin

try:
    import psycopg
except ImportError:
    psycopg = None

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = ROOT / "data"
SERIAL_DB = DATA_DIR / "serials.json"


class JsonSerialStorage:
    def __init__(self, path: Path):
        self.path = path
        self.admin_path = self.path.parent / "admin.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            else:
                self.data = {}
        except Exception:
            self.data = {}

    def _save(self):
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def exists(self, serial: str) -> bool:
        return serial in self.data

    def add(self, serial: str) -> dict:
        now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        entry = {"serial": serial, "purchasedAt": now}
        self.data[serial] = entry
        self._save()
        return entry

    def list(self):
        return list(self.data.keys())

    def get_admin_hash(self):
        try:
            if not self.admin_path.exists():
                return ""
            payload = json.loads(self.admin_path.read_text(encoding="utf-8"))
            return str(payload.get("password_hash", ""))
        except Exception:
            return ""

    def set_admin_hash(self, value: str):
        self.admin_path.write_text(json.dumps({"password_hash": value}, ensure_ascii=False), encoding="utf-8")


class PostgresSerialStorage:
    def __init__(self, database_url: str):
        if psycopg is None:
            raise RuntimeError("DATABASE_URL이 설정되었지만 psycopg가 설치되지 않았습니다.")
        self.database_url = database_url
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS serials (
                    serial TEXT PRIMARY KEY,
                    purchased_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS admin_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

    def _connect(self):
        return psycopg.connect(self.database_url, autocommit=True)

    def exists(self, serial: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM serials WHERE serial = %s", (serial,)
            ).fetchone()
            return row is not None

    def add(self, serial: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "INSERT INTO serials (serial) VALUES (%s) RETURNING purchased_at",
                (serial,),
            ).fetchone()
        return {"serial": serial, "purchasedAt": row[0].isoformat()}

    def list(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT serial, purchased_at FROM serials ORDER BY purchased_at DESC"
            ).fetchall()
            return [{"serial": row[0], "purchasedAt": row[1].isoformat()} for row in rows]

    def get_admin_hash(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT setting_value FROM admin_settings WHERE setting_key = 'password_hash'"
            ).fetchone()
            return row[0] if row else ""

    def set_admin_hash(self, value: str):
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO admin_settings (setting_key, setting_value)
                VALUES ('password_hash', %s)
                ON CONFLICT (setting_key) DO UPDATE
                SET setting_value = EXCLUDED.setting_value, updated_at = NOW()
            """, (value,))


def create_storage():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        return JsonSerialStorage(SERIAL_DB)
    try:
        return PostgresSerialStorage(database_url)
    except Exception as exc:
        print(f"Warning: DATABASE_URL unavailable; falling back to JSON storage: {exc}")
        return JsonSerialStorage(SERIAL_DB)


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
storage = create_storage()

PBKDF2_ITERATIONS = 500_000
ADMIN_SESSIONS = {}
SESSION_LOCK = threading.Lock()
SESSION_SECONDS = 8 * 60 * 60


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        iterations, salt_text, hash_text = encoded.split("$", 2)
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(hash_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    with SESSION_LOCK:
        ADMIN_SESSIONS[token] = time.time() + SESSION_SECONDS
    return token


def is_valid_session(token: str) -> bool:
    with SESSION_LOCK:
        expires = ADMIN_SESSIONS.get(token, 0)
        if expires <= time.time():
            ADMIN_SESSIONS.pop(token, None)
            return False
        return True


def initialize_admin():
    initial_password = os.environ.get("ADMIN_PASSWORD", "")
    if hasattr(storage, "get_admin_hash") and hasattr(storage, "set_admin_hash"):
        if not storage.get_admin_hash() and initial_password:
            storage.set_admin_hash(hash_password(initial_password))


initialize_admin()


factory_twin = FactoryDigitalTwin()


def real_cylinder_rows(limit: int = 120) -> list[dict]:
    """Read real Pi measurements locally, or their Supabase mirror on Render."""
    database_path = Path(os.environ.get(
        "SENSOR_DATABASE_PATH", str(ROOT / "data" / "smart_cylinder.db")
    ))
    if database_path.exists():
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT m.measured_at,m.sequence,m.cylinder_state,
                       vf.rms AS vibration_rms,sf.rms AS sound_rms,
                       c.prediction,c.confidence,c.health_score,
                       c.remaining_life_percent,c.remaining_hours,c.remaining_cycles,
                       c.rul_status,c.rul_model_version,
                       vr.prediction AS vibration_prediction,vr.confidence AS vibration_confidence,
                       sr.prediction AS sound_prediction,sr.confidence AS sound_confidence,
                       c.controlling_role,c.fusion_version
                FROM combined_results AS c
                JOIN measurements AS m
                  ON m.measurement_id=c.vibration_measurement_id
                JOIN feature_data AS vf
                  ON vf.measurement_id=c.vibration_measurement_id
                JOIN feature_data AS sf
                  ON sf.measurement_id=c.sound_measurement_id
                JOIN ml_results AS vr ON vr.measurement_id=c.vibration_measurement_id
                JOIN ml_results AS sr ON sr.measurement_id=c.sound_measurement_id
                ORDER BY c.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    table = os.environ.get("SUPABASE_TABLE", "smart_cylinder_analysis").strip()
    if not (url and key):
        return []
    from supabase import create_client
    response = (
        create_client(url, key).table(table)
        .select("measured_at,cylinder_state,vibration_rms,sound_rms,prediction,confidence,health_score,model_version")
        .order("measured_at", desc=True).limit(limit).execute()
    )
    return list(reversed(response.data or []))

SERIAL_PATTERN = re.compile(r"^SCC-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def normalize_serial(value: str) -> str:
    text = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    if len(text) != 11 or not text.startswith("SCC"):
        return ""
    return f"{text[:3]}-{text[3:7]}-{text[7:]}"


def generate_serial() -> str:
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    part = lambda: "".join(random.choice(chars) for _ in range(4))
    return f"SCC-{part()}-{part()}"


def read_json_payload():
    return request.get_json(silent=True) or {}


def bearer_token():
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


def require_admin():
    if is_valid_session(bearer_token()):
        return True
    return False


def build_cors_response(response):
    allowed = os.environ.get("ALLOWED_ORIGIN", "https://kang0840.github.io")
    origin = request.headers.get("Origin", "")
    if origin == allowed or (not DATABASE_URL and origin.startswith("http://localhost")):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Vary"] = "Origin"
    return response


# Flask must receive Python's built-in ``__name__`` value.  The public files
# are served by ``serve_static`` below, so no custom Flask static directory is
# needed here.
app = Flask(__name__)
app.after_request(build_cors_response)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "database": "postgres" if DATABASE_URL else "json"})


@app.route("/api/validate")
def api_validate():
    serial = normalize_serial(request.args.get("serial", ""))
    return jsonify({"serial": serial, "valid": bool(serial and storage.exists(serial))})


@app.route("/api/conveyor")
def api_conveyor():
    equipment_id = request.args.get("conveyor_id", "").strip().upper()
    if equipment_id:
        try:
            return jsonify(factory_twin.read_conveyor(equipment_id))
        except KeyError:
            return jsonify({"error": "unknown_conveyor", "available": list(factory_twin.conveyors)}), 404
    return jsonify({
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "conveyors": [factory_twin.read_conveyor(item) for item in factory_twin.conveyors],
    })


@app.route("/api/cylinder")
def api_cylinder():
    equipment_id = request.args.get("cylinder_id", "").strip().upper()
    if equipment_id:
        try:
            return jsonify(factory_twin.read_cylinder(equipment_id))
        except KeyError:
            return jsonify({"error": "unknown_cylinder", "available": list(factory_twin.cylinders)}), 404
    return jsonify({
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cylinders": [factory_twin.read_cylinder(item) for item in factory_twin.cylinders],
    })


@app.route("/api/history")
def api_factory_history():
    equipment_type = request.args.get("type", "").strip().lower()
    if equipment_type not in {"", "conveyor", "cylinder"}:
        return jsonify({"error": "invalid_type", "allowed": ["conveyor", "cylinder"]}), 400
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        return jsonify({"error": "invalid_limit"}), 400
    if not 1 <= limit <= 300:
        return jsonify({"error": "invalid_limit", "range": [1, 300]}), 400
    return jsonify({
        "type": equipment_type or "all",
        "history": factory_twin.history_items(equipment_type, limit),
    })


@app.route("/api/real-cylinder")
def api_real_cylinder():
    try:
        limit = max(1, min(300, int(request.args.get("limit", "120"))))
        rows = real_cylinder_rows(limit)
    except (ValueError, sqlite3.Error) as exc:
        return jsonify({"error": "real_data_unavailable", "message": str(exc)}), 503
    position = 0
    for row in rows:
        state = str(row.get("cylinder_state", "idle"))
        if state == "forward":
            position += 1
        elif state == "backward":
            position -= 1
        row["direction_value"] = 1 if state == "forward" else -1 if state == "backward" else 0
        row["position_index"] = position
    return jsonify({
        "source": "real_sensor_database",
        "count": len(rows),
        "latest": rows[-1] if rows else None,
        "history": rows,
    })


@app.route("/api/admin/serials")
def admin_serials():
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"serials": storage.list()})


@app.route("/api/purchase", methods=["POST"])
def api_purchase():
    serial = ""
    for _ in range(20):
        candidate = generate_serial()
        if not storage.exists(candidate):
            serial = candidate
            break
    if not serial:
        return jsonify({"error": "serial_error", "message": "새 시리얼을 생성할 수 없습니다."}), 500
    return jsonify(storage.add(serial))


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    password_hash = storage.get_admin_hash() if hasattr(storage, "get_admin_hash") else ""
    if not password_hash:
        return jsonify({"error": "admin_not_configured"}), 503
    password = str(read_json_payload().get("password", ""))
    if not verify_password(password, password_hash):
        return jsonify({"error": "invalid_credentials"}), 401
    return jsonify({"token": create_session(), "expiresIn": SESSION_SECONDS})


@app.route("/api/admin/password", methods=["POST"])
def admin_password():
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 401
    data = read_json_payload()
    current = str(data.get("currentPassword", ""))
    new_password = str(data.get("newPassword", ""))
    if not verify_password(current, storage.get_admin_hash()):
        return jsonify({"error": "invalid_current_password"}), 400
    if len(new_password) < 10:
        return jsonify({"error": "weak_password", "message": "비밀번호는 10자 이상이어야 합니다."}), 400
    storage.set_admin_hash(hash_password(new_password))
    with SESSION_LOCK:
        ADMIN_SESSIONS.clear()
    return jsonify({"changed": True})


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    with SESSION_LOCK:
        ADMIN_SESSIONS.pop(bearer_token(), None)
    return jsonify({"loggedOut": True})


@app.route("/<path:path>", methods=["OPTIONS"])
def handle_options(path):
    response = make_response("", 204)
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return build_cors_response(response)


@app.route("/", defaults={"path": "index.html"})
@app.route("/<path:path>")
def serve_static(path):
    if path in {"", "."}:
        path = "index.html"
    return send_from_directory(PUBLIC_DIR, path)


def main():
    parser = argparse.ArgumentParser(description="Smart Cylinder API + static server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    print(f"Serving {PUBLIC_DIR}")
    print(f"Open http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
