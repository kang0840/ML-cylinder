"""Pi deployment adapter; all analysis comes from the installed common Wheel."""

import argparse
import json
import logging
import os
import signal
import threading
from importlib import metadata
from pathlib import Path


def load_environment(path):
    """Read only the existing external configuration; never echo credentials."""
    allowed = {
        "MQTT_PASSWORD",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SENSOR_REALTIME_CONFIG",
        "SENSOR_STFT_CONFIG",
        "SENSOR_PREVIEW_CONFIG",
    }
    found = set()
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key = key.strip()
        if key in allowed:
            if key in found:
                raise ValueError("Duplicate external setting")
            found.add(key)
            os.environ[key] = value.strip().strip("'\"")


def configure_interactive(input_fn=input):
    """Choose one immutable experiment; no credentials or sensor thresholds."""
    device = input_fn("Pico ID (pico01..pico06): ").strip().lower()
    if device not in tuple("pico%02d" % n for n in range(1, 7)):
        raise ValueError("Invalid Pico ID")
    experiment = input_fn("Experiment ID: ").strip()
    if (
        not experiment
        or len(experiment) > 128
        or not all(
            character.isascii() and (character.isalnum() or character in "_-.")
            for character in experiment
        )
    ):
        raise ValueError("Invalid experiment ID")
    label = input_fn("Label: 1=NORMAL, 2=SEAL_LEAK: ").strip()
    if label not in ("1", "2"):
        raise ValueError("Explicit experiment label required")
    truth = "NORMAL" if label == "1" else "SEAL_LEAK"
    cylinder = "cylinder_" + device[-2:]
    print("EXPERIMENT:", experiment, "DEVICE:", device, "LABEL:", truth, flush=True)
    print("Stop old collectors. Keep Pico OFF until WAITING_FOR_SENSOR.", flush=True)
    if (
        input_fn("Confirm actual cylinder condition and new session (YES): ").strip()
        != "YES"
    ):
        raise ValueError("Collection not confirmed")
    os.environ.update(
        COLLECTION_MODE="TRAINING",
        TRAINING_CYLINDER_ID=cylinder,
        TRAINING_GROUND_TRUTH=truth,
        COLLECTION_EXPERIMENT_ID=experiment,
    )
    configure_resource_limits()


def configure_resource_limits():
    """Reuse approved bounded resources; never supply signal thresholds."""
    # Resource limits reused from the approved Raw collection configuration.
    # No STFT, similarity, cycle or fault thresholds are invented.
    if not os.environ.get("SENSOR_REALTIME_CONFIG"):
        os.environ["SENSOR_REALTIME_CONFIG"] = json.dumps(
            {
                "queue_max_size": 64,
                "max_payload_bytes": 65536,
                "dedup_max_sessions": 64,
                "dedup_ttl_seconds": 3600,
                "sph0645_max_samples": 4000,
                "inmp441_max_samples": 16000,
                "buffer_max_age_seconds": 60,
                "queue_max_age_seconds": 60,
                "shutdown_timeout_seconds": 30,
            }
        )


def create_runtime():
    from system.Backend.Server.sensor_runtime import (
        RealtimeConfig,
        STFTConfig,
        SensorRuntime,
    )
    from system.Backend.Service.cylinder_result_service import CylinderResultService
    from system.ML.Condition.analysis import WebPreviewConfig

    required = (
        "COLLECTION_MODE",
        "MQTT_PASSWORD",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SENSOR_REALTIME_CONFIG",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("CONFIG_REQUIRED: " + ", ".join(missing))
    realtime = RealtimeConfig(**json.loads(os.environ["SENSOR_REALTIME_CONFIG"]))
    config = json.loads(os.environ.get("SENSOR_STFT_CONFIG", "null"))
    if config is not None and set(config) != {"sph0645", "inmp441"}:
        raise ValueError("CONFIG_REQUIRED: sensor STFT mapping")
    stft = (
        {name: STFTConfig(**values) for name, values in config.items()}
        if config is not None
        else None
    )
    for name, maximum in (
        ("sph0645", realtime.sph0645_max_samples),
        ("inmp441", realtime.inmp441_max_samples),
    ):
        if stft is not None and stft[name].window_size > maximum:
            raise ValueError("CONFIG_REQUIRED: buffer smaller than STFT window")
    preview = os.environ.get("SENSOR_PREVIEW_CONFIG")
    mode = os.environ["COLLECTION_MODE"]
    if mode == "TRAINING":
        for name in (
            "TRAINING_CYLINDER_ID",
            "TRAINING_GROUND_TRUTH",
            "COLLECTION_EXPERIMENT_ID",
        ):
            if not os.environ.get(name):
                raise ValueError("CONFIG_REQUIRED: " + name)
    service = CylinderResultService(
        training_cylinder_id=(
            os.environ.get("TRAINING_CYLINDER_ID") if mode == "TRAINING" else None
        ),
        training_ground_truth=(
            os.environ.get("TRAINING_GROUND_TRUTH") if mode == "TRAINING" else None
        ),
        experiment_id=(
            os.environ.get("COLLECTION_EXPERIMENT_ID") if mode == "TRAINING" else None
        ),
    )
    return SensorRuntime(
        service=service,
        collection_mode=mode,
        realtime_config=realtime,
        stft_config=stft,
        preview_config=(
            WebPreviewConfig(**json.loads(preview))
            if preview and stft is not None
            else None
        ),
        raw_collection_only=stft is None,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--interactive", action="store_true")
    modes.add_argument("--operation", action="store_true")
    parser.add_argument("--env-file", default="/opt/smart-cylinder-pi5/.env")
    args = parser.parse_args()
    if metadata.version("smart-cylinder-common") != "0.1.8":
        print("PACKAGE_REQUIRED: smart-cylinder-common 0.1.8", flush=True)
        return 2
    lock = None
    try:
        if not args.check_config:
            import fcntl

            lock = open("/tmp/smart-cylinder-collection.lock", "a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("COLLECTOR_ALREADY_RUNNING", flush=True)
                return 2
        load_environment(args.env_file)
        if args.interactive:
            configure_interactive()
        elif args.operation:
            os.environ["COLLECTION_MODE"] = "OPERATION"
            for key in (
                "TRAINING_CYLINDER_ID",
                "TRAINING_GROUND_TRUTH",
                "COLLECTION_EXPERIMENT_ID",
            ):
                os.environ.pop(key, None)
            configure_resource_limits()
        runtime = create_runtime()
    except Exception as error:
        # Never print arbitrary exception text: injected config may contain secrets.
        required = (
            "COLLECTION_MODE",
            "MQTT_PASSWORD",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY",
            "SENSOR_REALTIME_CONFIG",
        )
        if os.environ.get("COLLECTION_MODE") == "TRAINING":
            required += (
                "TRAINING_CYLINDER_ID",
                "TRAINING_GROUND_TRUTH",
                "COLLECTION_EXPERIMENT_ID",
            )
        missing = [key for key in required if not os.environ.get(key)]
        print(
            "CONFIG_REQUIRED:", ", ".join(missing) or type(error).__name__, flush=True
        )
        return 2
    print("CONFIG: PASS; REFERENCE_REQUIRED; MODEL_REQUIRED", flush=True)
    if runtime.collection_mode == "OPERATION":
        print(
            "OPERATION: RAW_OPERATION; no training label; packet RMS/Peak enabled; lifetime DATA_REQUIRED",
            flush=True,
        )
    if runtime.collection_mode == "TRAINING":
        print(
            "TRAINING:",
            os.environ["TRAINING_CYLINDER_ID"],
            os.environ["TRAINING_GROUND_TRUTH"],
            "EXPERIMENT:",
            os.environ["COLLECTION_EXPERIMENT_ID"],
            "NEW_SESSION_REQUIRED; power on/reboot selected Pico only after subscription",
            flush=True,
        )
    print(
        "STFT:",
        "CONFIG_REQUIRED" if runtime.stft_config is None else "CONFIGURED",
        flush=True,
    )
    if args.check_config:
        print("Supabase credentials: SET; connection/write UNVERIFIED", flush=True)
        return 0

    import paho.mqtt.client as mqtt
    from system.MQTT.client import PiMqttClient

    stopped = threading.Event()
    failed = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    connection = PiMqttClient(
        client, subscriber=runtime, password=os.environ["MQTT_PASSWORD"]
    )

    def on_connect(_client, _userdata, _flags, reason, _properties):
        if reason == 0:
            print("MQTT CONNECTED", flush=True)
            connection.on_reconnect()
        else:
            print("MQTT AUTH/CONNECTION FAILED", flush=True)
            failed.set()
            stopped.set()

    def on_subscribe(_client, _userdata, _mid, reasons, _properties):
        if reasons and all(not reason.is_failure for reason in reasons):
            print("SUBSCRIPTION ACKNOWLEDGED; WAITING_FOR_SENSOR", flush=True)
        else:
            print("MQTT SUBSCRIPTION FAILED", flush=True)
            failed.set()
            stopped.set()

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    logging.basicConfig(level=logging.INFO)
    runtime.start()
    print("Runtime started; Analysis worker started; Queue ready", flush=True)
    print(
        "Raw worker ready; storage/sensor quality UNVERIFIED; DEVICE_BIAS_RISK",
        flush=True,
    )
    result = 0
    try:
        connection.connect_and_subscribe()
        client.loop_start()
        while not stopped.wait(1):
            pass
    except Exception as error:
        print("RUNTIME_ERROR:", type(error).__name__, flush=True)
        result = 1
    finally:
        try:
            client.disconnect()
            client.loop_stop()
        finally:
            if not runtime.stop():
                print("PERSISTENCE_GAP: shutdown incomplete", flush=True)
                result = 1
    return 1 if failed.is_set() else result


if __name__ == "__main__":
    raise SystemExit(main())
