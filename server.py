from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import argparse
import os
import json
import random
import datetime
import re

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = ROOT / "data"
SERIAL_DB = DATA_DIR / "serials.json"

class SerialStorage:
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

storage = SerialStorage(SERIAL_DB)

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
        if parsed.path.startswith("/api/"):
            return self.handle_api_get(parsed)
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/purchase":
            return self.handle_api_purchase()
        return super().do_POST()

    def send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_api_get(self, parsed):
        path = parsed.path
        query = parse_qs(parsed.query)
        serial = normalize_serial(query.get("serial", [""])[0])

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
    parser.add_argument("--port", type=int, default=8000)
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
