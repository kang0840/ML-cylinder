from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import argparse
import os
import json
import random
import datetime
import re
import hashlib
import secrets
import time
import base64
import threading

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


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
storage = PostgresSerialStorage(DATABASE_URL) if DATABASE_URL else JsonSerialStorage(SERIAL_DB)

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

def initialize_admin():
    if not DATABASE_URL:
        return
    initial_password = os.environ.get("ADMIN_PASSWORD", "")
    if not storage.get_admin_hash() and initial_password:
        storage.set_admin_hash(hash_password(initial_password))

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
                "noArrival": state["noArrival"]
            },
            "machining": {
                "pressure": round(state["machPressure"], 1),
                "cycles": state["machCycles"],
                "temperature": round(state["machTemp"], 1),
                "position": state["machPos"],
                "fault": state["machFault"]
            },
            "conveyor": {
                "speed": round(state["convSpeed"], 1),
                "count": state["convCount"]
            },
            "vision": {
                "good": state["vGood"],
                "bad": state["vBad"],
                "rate": round(state["vGood"] / max(1, state["vGood"] + state["vBad"]) * 100, 1),
                "lastResult": state["lastResult"]
            },
            "event": state["lastEvent"],
            "system": {
                "status": "run" if not state["machFault"] else "fault"
            }
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
            "lastEvent": "시스템이 초기화되었습니다. 모니터링을 시작합니다."
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


class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"status": "ok", "database": "postgres" if DATABASE_URL else "json"})
            return
        if parsed.path.startswith("/api/"):
            return self.handle_api_get(parsed)
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/purchase":
            return self.handle_api_purchase()
        if parsed.path == "/api/admin/login":
            return self.handle_admin_login()
        if parsed.path == "/api/admin/password":
            return self.handle_admin_password()
        if parsed.path == "/api/admin/logout":
            return self.handle_admin_logout()
        return super().do_POST()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def send_cors_headers(self):
        allowed = os.environ.get("ALLOWED_ORIGIN", "https://kang0840.github.io")
        origin = self.headers.get("Origin", "")
        if origin == allowed or (not DATABASE_URL and origin.startswith("http://localhost")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_api_get(self, parsed):
        path = parsed.path
        query = parse_qs(parsed.query)
        serial = normalize_serial(query.get("serial", [""])[0])

        if path == "/api/admin/serials":
            if not self.require_admin():
                return
            self.send_json({"serials": storage.list()})
            return

        if path == "/api/validate":
            self.send_json({"serial": serial, "valid": bool(serial and storage.exists(serial))})
            return

        if path == "/api/metrics":
            if not serial or not storage.exists(serial):
                self.send_json({"error": "invalid_serial", "message": "유효한 시리얼 넘버를 제공해야 합니다."}, status=400)
                return
            self.send_json(metrics_state.get(serial))
            return

        self.send_error(404, "Not Found")

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def bearer_token(self):
        header = self.headers.get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else ""

    def require_admin(self):
        if is_valid_session(self.bearer_token()):
            return True
        self.send_json({"error": "unauthorized"}, status=401)
        return False

    def handle_admin_login(self):
        password_hash = storage.get_admin_hash() if DATABASE_URL else ""
        if not password_hash:
            self.send_json({"error": "admin_not_configured"}, status=503)
            return
        password = str(self.read_json().get("password", ""))
        if not verify_password(password, password_hash):
            self.send_json({"error": "invalid_credentials"}, status=401)
            return
        self.send_json({"token": create_session(), "expiresIn": SESSION_SECONDS})

    def handle_admin_password(self):
        if not self.require_admin():
            return
        data = self.read_json()
        current = str(data.get("currentPassword", ""))
        new_password = str(data.get("newPassword", ""))
        if not verify_password(current, storage.get_admin_hash()):
            self.send_json({"error": "invalid_current_password"}, status=400)
            return
        if len(new_password) < 10:
            self.send_json({"error": "weak_password", "message": "비밀번호는 10자 이상이어야 합니다."}, status=400)
            return
        storage.set_admin_hash(hash_password(new_password))
        with SESSION_LOCK:
            ADMIN_SESSIONS.clear()
        self.send_json({"changed": True})

    def handle_admin_logout(self):
        with SESSION_LOCK:
            ADMIN_SESSIONS.pop(self.bearer_token(), None)
        self.send_json({"loggedOut": True})

    def handle_api_purchase(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)

        serial = ""
        for _ in range(20):
            candidate = generate_serial()
            if not storage.exists(candidate):
                serial = candidate
                break
        if not serial:
            self.send_json({"error": "serial_error", "message": "새 시리얼을 생성할 수 없습니다."}, status=500)
            return

        entry = storage.add(serial)
        self.send_json(entry)


def main():
    parser = argparse.ArgumentParser(description="Smart Cylinder API + static server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()

    os.chdir(PUBLIC_DIR)
    server = ThreadingHTTPServer((args.host, args.port), StaticHandler)
    print(f"Serving {PUBLIC_DIR}")
    print(f"Open http://{args.host}:{args.port}/")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("서버가 종료되었습니다.")


if __name__ == "__main__":
    main()
