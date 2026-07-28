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
import threading
import time

from flask import Flask, jsonify, make_response, request, send_from_directory

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


class MetricsState:
    def __init__(self):
        self.states = {}

    def get(self, serial: str) -> dict:
        state = self.states.get(serial)
        if state is None:
            state = self._initialize(serial)
            self.states[serial] = state
        self._advance(state)
        return {
            "serial": serial,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "discharge": {
                "good": state["good"],
                "defect": state["defect"],
                "noArrival": state["noArrival"],
            },
            "machining": {
                "pressure": round(state["machPressure"], 1),
                "cycles": state["machCycles"],
                "temperature": round(state["machTemp"], 1),
                "position": state["machPos"],
                "fault": state["machFault"],
            },
            "conveyor": {
                "speed": round(state["convSpeed"], 1),
                "count": state["convCount"],
            },
            "vision": {
                "good": state["vGood"],
                "bad": state["vBad"],
                "rate": round(state["vGood"] / max(1, state["vGood"] + state["vBad"]) * 100, 1),
                "lastResult": state["lastResult"],
            },
            "event": state["lastEvent"],
            "system": {"status": "run" if not state["machFault"] else "fault"},
        }

    def _initialize(self, serial: str) -> dict:
        seed = sum(ord(ch) for ch in serial)
        rng = random.Random(seed)
        return {
            "good": rng.randint(8, 18),
            "defect": rng.randint(0, 4),
            "noArrival": rng.randint(0, 2),
            "machCycles": rng.randint(20, 36),
            "machTemp": rng.uniform(42.0, 47.0),
            "machPressure": rng.uniform(3.6, 4.8),
            "machPos": rng.randint(35, 70),
            "machFault": False,
            "convSpeed": rng.uniform(1.1, 1.7),
            "convCount": rng.randint(120, 220),
            "vGood": rng.randint(26, 36),
            "vBad": rng.randint(0, 6),
            "lastResult": "GOOD",
            "lastEvent": "시스템이 초기화되었습니다. 모니터링을 시작합니다.",
        }

    def _advance(self, state: dict):
        state["good"] += random.randint(0, 2)
        state["defect"] += random.choice((0, 0, 1))
        state["noArrival"] += random.choice((0, 0, 0, 1))
        state["machCycles"] += 1
        state["machTemp"] = max(38.0, min(58.0, state["machTemp"] + random.uniform(-0.4, 0.5)))
        state["machPressure"] = max(2.5, min(6.5, state["machPressure"] + random.uniform(-0.4, 0.4)))
        state["machPos"] = min(100, max(0, state["machPos"] + random.randint(-3, 5)))
        state["convSpeed"] = max(0.9, min(2.2, state["convSpeed"] + random.uniform(-0.12, 0.12)))
        state["convCount"] += random.randint(1, 4)
        if random.random() < 0.18 or state["machTemp"] > 53.5:
            state["machFault"] = True
        elif random.random() < 0.16:
            state["machFault"] = False
        outcome = "GOOD" if random.random() < 0.84 else "DEFECT"
        state["lastResult"] = outcome
        if outcome == "GOOD":
            state["vGood"] += 1
            state["lastEvent"] = "비전 검사 완료 · 양품 PASS"
        else:
            state["vBad"] += 1
            state["lastEvent"] = "비전 검사 완료 · 불량 FAIL"
        if state["machFault"]:
            state["lastEvent"] = "⚠ 가공 실린더 이상 감지 · 점검 필요"


metrics_state = MetricsState()

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


@app.route("/api/metrics")
def api_metrics():
    serial = normalize_serial(request.args.get("serial", ""))
    if not serial or not storage.exists(serial):
        return jsonify({"error": "invalid_serial", "message": "유효한 시리얼 넘버를 제공해야 합니다."}), 400
    return jsonify(metrics_state.get(serial))


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
