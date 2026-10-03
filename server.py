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
import uuid

from flask import Flask, jsonify, make_response, request, send_from_directory

from src.factory_twin import FactoryDigitalTwin
from system.Backend.API.cylinder_result import cylinder_result_blueprint

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

    def add(self, serial: str, purchased_at: str | None = None) -> dict:
        with self._connect() as connection:
            if purchased_at:
                row = connection.execute(
                    """INSERT INTO serials (serial, purchased_at) VALUES (%s, %s)
                    ON CONFLICT (serial) DO NOTHING RETURNING purchased_at""",
                    (serial, purchased_at),
                ).fetchone()
            else:
                row = connection.execute(
                    "INSERT INTO serials (serial) VALUES (%s) RETURNING purchased_at",
                    (serial,),
                ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT purchased_at FROM serials WHERE serial = %s", (serial,)
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


class ProcessOrderNotFoundError(RuntimeError):
    """Raised when a process order does not exist."""


class ProcessOrderStateError(RuntimeError):
    """Raised when an order is not ready for the requested transition."""


class ProcessOrderConflictError(RuntimeError):
    """Raised when a completed manual input is changed."""


class PostgresProcessOrderStorage:
    """Store A/B process orders and manual judgments in Supabase Postgres."""

    ORDER_SELECT = """
        SELECT o.order_id::text AS order_id,
               o.product_name,
               o.requested_product,
               o.status,
               o.created_at,
               o.started_at,
               o.completed_at,
               o.start_dispatch_state,
               o.start_published_at,
               j.detected_product,
               j.judgment,
               j.detection_source,
               j.created_at AS judgment_created_at,
               j.dispatch_state AS judgment_dispatch_state,
               j.published_at AS judgment_published_at
        FROM process_orders AS o
        LEFT JOIN process_judgments AS j ON j.order_id = o.order_id
    """

    def __init__(self, database_url: str):
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is configured but psycopg is unavailable")
        self.database_url = database_url

    def _connect(self):
        return psycopg.connect(self.database_url)

    @staticmethod
    def _row(cursor, row):
        if row is None:
            return None
        result = dict(zip((column.name for column in cursor.description), row))
        for key, value in tuple(result.items()):
            if isinstance(value, (datetime.date, datetime.datetime, uuid.UUID)):
                result[key] = (
                    value.isoformat() if hasattr(value, "isoformat") else str(value)
                )
        return result

    def create_order(self, product_name: str, requested_product: str) -> dict:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO process_orders (product_name, requested_product)
                VALUES (%s, %s)
                RETURNING order_id::text
                """,
                (product_name, requested_product),
            )
            order_id = cursor.fetchone()[0]
        return self.get_order(order_id)

    def list_orders(self, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            cursor = connection.execute(
                self.ORDER_SELECT + " ORDER BY o.created_at DESC LIMIT %s", (limit,)
            )
            return [self._row(cursor, row) for row in cursor.fetchall()]

    def get_order(self, order_id: str) -> dict:
        with self._connect() as connection:
            cursor = connection.execute(
                self.ORDER_SELECT + " WHERE o.order_id = %s", (order_id,)
            )
            row = self._row(cursor, cursor.fetchone())
        if row is None:
            raise ProcessOrderNotFoundError("process order not found")
        return row

    def create_manual_judgment(
        self, order_id: str, detected_product: str
    ) -> tuple[dict, bool]:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                SELECT requested_product, status
                FROM process_orders
                WHERE order_id = %s
                FOR UPDATE
                """,
                (order_id,),
            )
            order = cursor.fetchone()
            if order is None:
                raise ProcessOrderNotFoundError("process order not found")

            existing = connection.execute(
                """
                SELECT detected_product
                FROM process_judgments
                WHERE order_id = %s
                """,
                (order_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != detected_product:
                    raise ProcessOrderConflictError(
                        "manual detection already recorded with a different value"
                    )
                created = False
            else:
                if order[1] != "STARTED":
                    raise ProcessOrderStateError(
                        "manual detection requires a STARTED order"
                    )
                judgment = "OK" if order[0] == detected_product else "NG"
                connection.execute(
                    """
                    INSERT INTO process_judgments (
                        order_id,
                        requested_product,
                        detected_product,
                        judgment,
                        detection_source
                    ) VALUES (%s, %s, %s, %s, 'MANUAL')
                    """,
                    (order_id, order[0], detected_product, judgment),
                )
                created = True
        return self.get_order(order_id), created


def create_storage():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        return JsonSerialStorage(SERIAL_DB)
    try:
        storage = PostgresSerialStorage(database_url)
        # Render's PostgreSQL starts empty; retain serials already saved in the
        # repository's local JSON source.  Repeated deploys cannot duplicate rows.
        try:
            legacy = json.loads(SERIAL_DB.read_text(encoding="utf-8"))
            for serial, entry in legacy.items():
                if isinstance(serial, str) and serial.startswith("SCC-"):
                    storage.add(serial, str(entry.get("purchasedAt", "")) or None)
        except (OSError, ValueError, AttributeError):
            pass
        return storage
    except Exception as exc:
        print(f"Warning: DATABASE_URL unavailable; falling back to JSON storage: {exc}")
        return JsonSerialStorage(SERIAL_DB)


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
storage = create_storage()
process_order_storage = PostgresProcessOrderStorage(DATABASE_URL) if DATABASE_URL else None

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


def real_cylinder_rows(limit: int = 120, data_source: str = "live") -> list[dict]:
    """Read live local Pi data when available, otherwise its Supabase mirror."""
    database_path = Path(os.environ.get(
        "SENSOR_DATABASE_PATH", str(ROOT / "data" / "smart_cylinder.db")
    ))
    # Replay rows intentionally never enter the local operational database.
    if data_source == "live" and database_path.exists():
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
        create_client(url, key)
        .table(table)
        .select(
            "measurement_id,measured_at,cylinder_state,"
            "vibration_rms,sound_rms,prediction,confidence,health_score,"
            "model_version,data_source,replay_source_measured_at,replay_run_id"
        )
        .eq("data_source", data_source)
        .order("measured_at", desc=True)
        .limit(limit)
        .execute()
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


def process_storage():
    if process_order_storage is None:
        raise RuntimeError("process order database is unavailable")
    return process_order_storage


def normalize_process_product(value):
    product = str(value or "").strip().upper()
    return product if product in {"A", "B"} else ""


def valid_order_id(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


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
app.register_blueprint(cylinder_result_blueprint)


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
        data_source = request.args.get("source", "live").strip().lower()
        if data_source not in {"live", "replay"}:
            return jsonify({"error": "invalid_source"}), 400
        rows = real_cylinder_rows(limit, data_source)
    except (ValueError, sqlite3.Error) as exc:
        return jsonify({"error": "real_data_unavailable", "message": str(exc)}), 503
    for row in rows:
        state = str(row.get("cylinder_state", "idle"))
        row["direction_value"] = 1 if state == "forward" else -1 if state == "backward" else 0
    return jsonify({
        "source": data_source,
        "count": len(rows),
        "latest": rows[-1] if rows else None,
        "history": rows,
    })


@app.route("/api/process-orders", methods=["GET", "POST"])
def api_process_orders():
    """Create an A/B order or list recent process orders."""
    try:
        order_storage = process_storage()
        if request.method == "GET":
            try:
                limit = int(request.args.get("limit", "50"))
            except ValueError:
                return jsonify({"error": "invalid_limit"}), 400
            if not 1 <= limit <= 100:
                return jsonify({"error": "invalid_limit", "range": [1, 100]}), 400
            return jsonify({"orders": order_storage.list_orders(limit)})

        payload = read_json_payload()
        product_name = str(payload.get("product_name", "")).strip()
        requested_product = normalize_process_product(payload.get("requested_product"))
        if not 1 <= len(product_name) <= 100:
            return jsonify({"error": "invalid_product_name"}), 400
        if not requested_product:
            return (
                jsonify({"error": "invalid_requested_product", "allowed": ["A", "B"]}),
                400,
            )
        return jsonify(order_storage.create_order(product_name, requested_product)), 201
    except RuntimeError as exc:
        return jsonify({"error": "process_order_unavailable", "message": str(exc)}), 503


@app.route("/api/process-orders/<order_id>")
def api_process_order(order_id):
    """Return one order together with its optional final judgment."""
    normalized = valid_order_id(order_id)
    if not normalized:
        return jsonify({"error": "invalid_order_id"}), 400
    try:
        return jsonify(process_storage().get_order(normalized))
    except ProcessOrderNotFoundError:
        return jsonify({"error": "process_order_not_found"}), 404
    except RuntimeError as exc:
        return jsonify({"error": "process_order_unavailable", "message": str(exc)}), 503


@app.route("/api/process-orders/<order_id>/manual-detection", methods=["POST"])
def api_manual_detection(order_id):
    """Record the temporary manual A/B detector result exactly once."""
    normalized = valid_order_id(order_id)
    if not normalized:
        return jsonify({"error": "invalid_order_id"}), 400
    detected_product = normalize_process_product(
        read_json_payload().get("detected_product")
    )
    if not detected_product:
        return (
            jsonify({"error": "invalid_detected_product", "allowed": ["A", "B"]}),
            400,
        )
    try:
        order, created = process_storage().create_manual_judgment(
            normalized, detected_product
        )
        return jsonify({**order, "created": created}), 201 if created else 200
    except ProcessOrderNotFoundError:
        return jsonify({"error": "process_order_not_found"}), 404
    except ProcessOrderStateError as exc:
        return jsonify({"error": "invalid_order_state", "message": str(exc)}), 409
    except ProcessOrderConflictError as exc:
        return jsonify({"error": "manual_detection_conflict", "message": str(exc)}), 409
    except RuntimeError as exc:
        return jsonify({"error": "process_order_unavailable", "message": str(exc)}), 503


@app.route("/api/admin/serials")
def admin_serials():
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"serials": storage.list()})


@app.route("/api/admin/serials", methods=["POST"])
def admin_add_serial():
    """Register an operator-supplied SCC serial after administrator login."""
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 401
    serial = normalize_serial(read_json_payload().get("serial", ""))
    if not serial:
        return jsonify({"error": "invalid_serial"}), 400
    if storage.exists(serial):
        return jsonify({"error": "serial_exists", "serial": serial}), 409
    return jsonify(storage.add(serial)), 201


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
    if path == "order-system.html":
        return send_from_directory(ROOT, path)
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
