"""Single local entry point for the API server and continuous AI inference."""

from __future__ import annotations

import argparse
import atexit
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
AI_SCRIPT = ROOT / "smart_cylinder_ai.py"


def start_ai_worker(interval: float) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        str(AI_SCRIPT),
        "--count", "0",
        "--interval", str(interval),
        "--excel", str(ROOT / "smart_cylinder_training.xlsx"),
        "--output", str(ROOT / "smart_cylinder_result.json"),
    ]
    return subprocess.Popen(command, cwd=ROOT, text=True)


def stop_worker(worker: subprocess.Popen[str] | None) -> None:
    if worker is None or worker.poll() is not None:
        return
    worker.terminate()
    try:
        worker.wait(timeout=5)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Factory all-in-one runner")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ai-interval", type=float, default=5.0)
    parser.add_argument("--no-ai-worker", action="store_true")
    args = parser.parse_args()
    if args.ai_interval <= 0:
        parser.error("ai-interval must be positive")

    # Importing the app initializes the independent conveyor and cylinder models.
    from server import PUBLIC_DIR, app

    worker = None if args.no_ai_worker else start_ai_worker(args.ai_interval)
    atexit.register(stop_worker, worker)
    print("Conveyor model: ready")
    print("Cylinder model: ready")
    print(f"Continuous Excel AI: {'disabled' if worker is None else 'running'}")
    print(f"Serving {PUBLIC_DIR}")
    print(f"Open http://{args.host}:{args.port}/")
    try:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    finally:
        stop_worker(worker)


if __name__ == "__main__":
    main()

