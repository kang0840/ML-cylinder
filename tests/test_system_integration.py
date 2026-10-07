"""Synthetic interface tests only; no hardware or live DB writes."""


def test_confirmed_session_cache_is_bounded_and_rejects_label_change():
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from system.DB.Supabase.cylinder_result_repository import (
        CylinderResultRepository,
        SessionConflictError,
    )

    client = MagicMock()
    query = client.table.return_value
    for name in ("select", "eq", "limit", "upsert"):
        getattr(query, name).return_value = query
    session = dict(
        session_id=str(uuid4()),
        cylinder_id="cylinder_01",
        collection_mode="OPERATION",
        dataset_type="RAW_OPERATION",
        ground_truth=None,
        ground_truth_source=None,
        experiment_id=None,
        started_at="2026-10-07T20:00:00+09:00",
    )
    query.execute.return_value = SimpleNamespace(data=[session])
    repo = CylinderResultRepository(client)
    repo.ensure_collection_session(session)
    assert query.execute.call_count == 2
    for _ in range(100):
        repo.ensure_collection_session(
            {**session, "started_at": "2026-10-07T20:01:00+09:00"}
        )
    assert query.execute.call_count == 2
    assert len(repo._confirmed_sessions) == 1
    with pytest.raises(SessionConflictError):
        repo.ensure_collection_session({**session, "ground_truth": "NORMAL"})
    assert query.execute.call_count == 2


def test_runtime_reuses_confirmed_raw_write_without_get_and_preserves_pcm():
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository

    client = MagicMock()
    query = client.table.return_value
    for name in ("select", "eq", "limit", "upsert", "update"):
        getattr(query, name).return_value = query
    row = payload()
    query.execute.return_value = SimpleNamespace(data=[{**row, "id": 55}])
    repo = CylinderResultRepository(client)
    repo.save_raw_data(row)
    metadata = dict(
        last_received_at=row["timestamp"],
        stft_status="COMPLETED",
        operation_status="REFERENCE_REQUIRED",
        ml_status="MODEL_REQUIRED",
        stft_preview=None,
    )
    repo.save_runtime_metadata(row, metadata)
    assert query.execute.call_count == 2  # INSERT + PATCH, no readback
    saved = query.update.call_args.args[0]["raw_payload"]
    assert saved["sph0645"] == row["sph0645"]
    assert saved["inmp441"] == row["inmp441"]
    assert saved["runtime"] == metadata


def test_monitoring_pending_latest_falls_back_within_session_even_limit_one():
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from datetime import datetime
    from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository

    client = MagicMock()
    query = client.table.return_value
    for name in ("select", "eq", "lte", "order", "limit"):
        getattr(query, name).return_value = query
    query.not_.is_.return_value = query
    session = str(uuid4())
    pending = dict(
        cylinder_id="cylinder_01",
        session_id=session,
        sequence_id=2,
        measured_at="2026-10-07T20:00:01+09:00",
        runtime=None,
    )
    completed = {
        **pending,
        "sequence_id": 1,
        "runtime": dict(
            last_received_at="2026-10-07T20:00:00+09:00", stft_status="COMPLETED"
        ),
    }
    query.execute.side_effect = [
        SimpleNamespace(data=data) for data in ([pending], [completed], [], [])
    ]
    view = CylinderResultService(CylinderResultRepository(client)).monitoring(
        "cylinder_01", 1, datetime.fromisoformat("2026-10-07T20:00:52+09:00")
    )
    assert view["latest"]["sequence_id"] == 1
    assert view["latest"]["stft_status"] == "COMPLETED"
    assert view["live_status"] == "STALE"  # never refresh receive time
    assert view["count"] == 1
    assert ("session_id", session) in [call.args for call in query.eq.call_args_list]


def test_pending_runtime_is_not_missing_stft_configuration():
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.read_monitoring.return_value = dict(
        rows=[
            dict(
                session_id=str(uuid4()),
                sequence_id=1,
                measured_at="2026-10-07T20:00:00+09:00",
                runtime=None,
            )
        ],
        features=None,
        prediction=None,
        preview=None,
    )
    view = CylinderResultService(repo).monitoring("cylinder_01", 1)
    assert view["latest"]["stft_status"] == "WAITING_FOR_DATA"
    assert view["live_status"] == "NO_DATA"


import base64
from copy import deepcopy
import struct
from uuid import uuid4

import pytest
import numpy as np
from system.MQTT.message_parser import parse_payload


def test_mean_removal_preserves_raw_and_matches_training_realtime_features():
    from system.ML.Condition.analysis import CycleFeatureExtractor, preprocess_samples

    raw = np.array([-450010, -450000, -449990, -450000], dtype=np.int32)
    original = raw.copy()
    centered = preprocess_samples(raw)
    np.testing.assert_array_equal(raw, original)
    np.testing.assert_array_equal(centered, [-10, 0, 10, 0])
    extractor = CycleFeatureExtractor()
    training = extractor.extract_model_features(raw.tolist(), raw.tolist())
    realtime = extractor.extract_model_features(raw, raw)
    assert training == realtime
    assert training["sound"]["mean"] == 0
    assert training["sound"]["rms"] == pytest.approx(np.sqrt(50))
    compact = extractor.extract(raw, raw)
    assert compact["inmp441_rms"] == training["sound"]["rms"]


@pytest.mark.parametrize("samples", [[], [float("nan")], [float("inf")], [[1]], [True]])
def test_mean_removal_rejects_invalid_input(samples):
    from system.ML.Condition.analysis import preprocess_samples

    with pytest.raises(ValueError):
        preprocess_samples(samples)


def test_legacy_model_cannot_silently_use_centered_features():
    from types import SimpleNamespace
    from system.ML.Condition.realtime_inference import (
        RealtimeInference,
        ModelContractError,
    )

    with pytest.raises(ModelContractError, match="preprocessing contract mismatch"):
        RealtimeInference()._validate_engine_contract(SimpleNamespace())


def test_packet_metrics_preserve_raw_and_are_not_cycle_features():
    from system.ML.Condition.analysis import make_packet_metrics
    from system.DB.Supabase.cylinder_result_repository import validate_packet_metrics

    row = parse_payload(payload())
    row["sph0645"]["samples"] = [-450010, -450000, -449990, -450000]
    before = deepcopy(row)
    metrics = make_packet_metrics(row)
    assert row == before
    assert validate_packet_metrics(metrics) == metrics
    assert metrics["sph0645"]["rms"] == pytest.approx(np.sqrt(50))
    assert metrics["sph0645"]["peak"] == 10
    assert "prediction" not in metrics and "samples" not in metrics["sph0645"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("rms", float("nan")),
        ("rms", -1),
        ("peak", True),
        ("sample_count", 0),
        ("sample_rate", 8000),
        ("samples", [1]),
    ],
)
def test_packet_metadata_rejects_invalid_or_raw_fields(field, value):
    from system.ML.Condition.analysis import make_packet_metrics
    from system.DB.Supabase.cylinder_result_repository import validate_packet_metrics

    metrics = make_packet_metrics(parse_payload(payload()))
    metrics["sph0645"][field] = value
    with pytest.raises(ValueError):
        validate_packet_metrics(metrics)


def test_operation_raw_only_has_packet_metrics_without_stft_or_model():
    repo = Repository()
    rt = SensorRuntime(
        CylinderResultService(repo),
        "OPERATION",
        realtime_config=realtime_limits(),
        raw_collection_only=True,
    )
    row = payload()
    before = deepcopy(row)
    assert rt.enqueue_message(__import__("json").dumps(row))
    assert rt.process_next()
    stored = repo.raw[0]
    decoded_before = parse_payload(deepcopy(before))
    for sensor in ("sph0645", "inmp441"):
        assert {
            key: value for key, value in stored[sensor].items() if key != "samples"
        } == before[sensor]
        assert stored[sensor]["samples"] == decoded_before[sensor]["samples"]
    assert stored["runtime"]["packet_metrics"]["sph0645"]["rms"] >= 0
    assert stored["runtime"]["stft_status"] == "CONFIG_REQUIRED"
    view = CylinderResultService(repo).monitoring("cylinder_01", 10)
    assert view["latest"]["packet_metrics"]
    assert view["latest"]["vibration_rms"] is None
    assert view["latest"]["prediction"] is None
    assert "raw_payload" not in view["latest"]
    assert rt.handle_message(row)[1] is True
    assert len(repo.raw) == 1
    assert repo.sessions[row["session_id"]]["ground_truth"] is None


def test_operation_adapter_clears_training_settings(monkeypatch, capsys):
    import runpy
    from pathlib import Path

    adapter = runpy.run_path(
        str(Path(__file__).parents[1] / "deploy" / "pi_sensor_runtime.py")
    )
    monkeypatch.setenv("TRAINING_GROUND_TRUTH", "NORMAL")
    monkeypatch.setenv("TRAINING_CYLINDER_ID", "cylinder_01")
    monkeypatch.setenv("COLLECTION_EXPERIMENT_ID", "old-experiment")
    monkeypatch.setattr(
        adapter["main"].__globals__["metadata"], "version", lambda _: "0.1.8"
    )
    monkeypatch.setattr(
        __import__("sys"), "argv", ["adapter", "--operation", "--check-config"]
    )

    def load(_):
        for key in ("MQTT_PASSWORD", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
            monkeypatch.setenv(key, "synthetic-not-a-secret")

    monkeypatch.setitem(adapter["main"].__globals__, "load_environment", load)
    monkeypatch.delenv("SENSOR_REALTIME_CONFIG", raising=False)
    assert adapter["main"]() == 0
    import os

    assert os.environ["COLLECTION_MODE"] == "OPERATION"
    assert "TRAINING_GROUND_TRUTH" not in os.environ
    assert "TRAINING_CYLINDER_ID" not in os.environ
    assert "COLLECTION_EXPERIMENT_ID" not in os.environ
    assert "synthetic-not-a-secret" not in capsys.readouterr().out


def test_packet_calculation_failure_does_not_stop_raw_storage(monkeypatch):
    import system.Backend.Server.sensor_runtime as runtime_module

    def fail(_):
        raise ValueError("synthetic analysis failure")

    monkeypatch.setattr(runtime_module, "make_packet_metrics", fail)
    repo = Repository()
    rt = SensorRuntime(
        CylinderResultService(repo),
        "OPERATION",
        realtime_config=realtime_limits(),
        raw_collection_only=True,
    )
    row = payload()
    rt.handle_message(row)
    row = deepcopy(row)
    row["sequence_id"] += 1
    rt.handle_message(row)
    assert len(repo.raw) == 2
    assert rt.metrics["PACKET_METRICS_ERROR"] == 2
    assert "packet_metrics" not in repo.raw[-1]["runtime"]


def test_runtime_metadata_accepts_packet_metrics_and_legacy_metadata():
    from system.DB.Supabase.cylinder_result_repository import validate_runtime_metadata
    from system.ML.Condition.analysis import make_packet_metrics

    metadata = dict(
        last_received_at="2026-10-07T10:00:00+09:00",
        stft_status="CONFIG_REQUIRED",
        operation_status="REFERENCE_REQUIRED",
        ml_status="MODEL_REQUIRED",
        stft_preview=None,
    )
    assert validate_runtime_metadata(metadata) == metadata
    metadata["packet_metrics"] = make_packet_metrics(parse_payload(payload()))
    assert validate_runtime_metadata(metadata) == metadata
    metadata["packet_metrics"]["prediction"] = "NORMAL"
    with pytest.raises(ValueError):
        validate_runtime_metadata(metadata)


def test_runtime_json_contract_is_separate_and_serializable():
    from system.DB.Supabase.cylinder_result_repository import validate_runtime_metadata

    metadata = dict(
        last_received_at="2026-10-06T10:00:00+09:00",
        stft_status="COMPLETED",
        operation_status="REFERENCE_REQUIRED",
        ml_status="WAITING_FOR_OPERATION",
        stft_preview=None,
    )
    assert validate_runtime_metadata(metadata) == metadata
    for changes in (
        {"ml_status": "NORMAL"},
        {"last_received_at": "2026-10-06T10:00:00"},
        {"prediction": "NORMAL"},
        {"stft_preview": {"sph0645": {}}},
    ):
        with pytest.raises((ValueError, TypeError)):
            validate_runtime_metadata(dict(metadata, **changes))


def test_repository_runtime_update_preserves_raw_and_identity():
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository

    row = payload()
    raw = CylinderResultRepository._compact_raw_payload(row)
    client = MagicMock()
    query = client.table.return_value
    for method in ("select", "eq", "limit", "update"):
        getattr(query, method).return_value = query
    query.execute.side_effect = [
        SimpleNamespace(data=[dict(id=1, raw_payload=raw)]),
        SimpleNamespace(data=[dict(id=1)]),
    ]
    metadata = dict(
        last_received_at="2026-10-06T10:00:00+09:00",
        stft_status="CONFIG_REQUIRED",
        operation_status="REFERENCE_REQUIRED",
        ml_status="MODEL_REQUIRED",
        stft_preview=None,
    )
    CylinderResultRepository(client).save_runtime_metadata(row, metadata)
    changed = query.update.call_args.args[0]
    assert set(changed) == {"raw_payload"}
    assert changed["raw_payload"].pop("runtime") == metadata
    assert changed["raw_payload"] == raw
    assert "runtime" not in raw
    assert all(
        ((name, row[name]), {}) in [(c.args, c.kwargs) for c in query.eq.call_args_list]
        for name in ("cylinder_id", "session_id", "sequence_id")
    )


def test_repository_monitoring_empty_is_not_zero_or_prediction():
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository

    client = MagicMock()
    query = client.table.return_value
    for method in ("select", "eq", "order", "limit"):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=[])
    result = CylinderResultRepository(client).read_monitoring("cylinder_01", 10)
    assert result == dict(rows=[], features=None, prediction=None, preview=None)
    assert ("collection_sessions.collection_mode", "OPERATION") in [
        c.args for c in query.eq.call_args_list
    ]
    assert "data" not in query.select.call_args.args[0].split(",")


from system.ML.Condition.analysis import calculate_stft


def test_preview_retention_prunes_only_preview_before_new_write():
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository

    client = MagicMock()
    query = client.table.return_value
    for method in ("select", "eq", "neq", "limit", "order", "range", "update"):
        getattr(query, method).return_value = query
    query.not_.is_.return_value = query
    row = payload()
    raw = CylinderResultRepository._compact_raw_payload(row)
    preview = {
        s: dict(relative_times=[0.1], frequencies=[100], magnitude=[[1]])
        for s in ("sph0645", "inmp441")
    }
    metadata = dict(
        last_received_at="2026-10-06T10:00:00+09:00",
        stft_status="COMPLETED",
        operation_status="REFERENCE_REQUIRED",
        ml_status="MODEL_REQUIRED",
        stft_preview=preview,
    )
    old = dict(raw, runtime=deepcopy(metadata))
    query.execute.side_effect = [
        SimpleNamespace(data=[dict(id=3, raw_payload=raw)]),
        SimpleNamespace(data=[dict(id=1, raw_payload=old)]),
        SimpleNamespace(data=[dict(id=1)]),
        SimpleNamespace(data=[]),
        SimpleNamespace(data=[dict(id=3)]),
    ]
    CylinderResultRepository(client).save_runtime_metadata(row, metadata, 2)
    first, second = [c.args[0]["raw_payload"] for c in query.update.call_args_list]
    assert first["runtime"]["stft_preview"] is None
    assert second["runtime"]["stft_preview"] == preview
    assert {k: v for k, v in first.items() if k != "runtime"} == raw
    assert all(set(c.args[0]) == {"raw_payload"} for c in query.update.call_args_list)
    assert not query.delete.called
    assert all(
        c.args == ("raw_payload->runtime->>stft_preview", "null")
        for c in query.not_.is_.call_args_list
    )


def test_runtime_storage_failure_is_logged_without_receiver_shutdown(caplog):
    repo = Repository()

    def fail(*args):
        raise RuntimeError("private detail")

    repo.save_runtime_metadata = fail
    rt = runtime(repo)
    assert rt.handle_message(payload())
    assert "RUNTIME_STORAGE_ERROR" in caplog.text
    assert "private detail" not in caplog.text
    assert len(repo.raw) == 1
    assert rt.handle_message(dict(payload(), sequence_id=2))
    assert len(repo.raw) == 2


def stft_config(**changes):
    """Synthetic-test parameters only, not sensor defaults."""
    return dict(
        window_size=400, hop_length=100, n_fft=400, window_function="hann", **changes
    )


def test_stft_synthetic_tone_and_shape():
    x = np.sin(2 * np.pi * 100 * np.arange(4000) / 4000)
    original = x.copy()
    result = calculate_stft(x, 4000, stft_config())
    assert result.magnitude.shape == (201, 37)
    np.testing.assert_array_equal(x, original)
    np.testing.assert_allclose(result.times, 0.05 + np.arange(37) * 0.025)
    np.testing.assert_allclose(result.frequencies, np.arange(201) * 10)
    peaks = result.frequencies[result.magnitude.argmax(axis=0)]
    np.testing.assert_allclose(peaks, 100)
    np.testing.assert_allclose(result.magnitude[10], 0.5, atol=1e-12)


def test_stft_synthetic_frequency_change():
    t = np.arange(8000) / 4000
    x = np.sin(2 * np.pi * np.where(t < 1, 100, 300) * t)
    result = calculate_stft(x, 4000, stft_config())
    peaks = result.frequencies[result.magnitude.argmax(axis=0)]
    np.testing.assert_allclose(peaks[result.times < 0.95], 100)
    np.testing.assert_allclose(peaks[result.times > 1.05], 300)
    assert abs(result.times[np.flatnonzero(peaks == 300)[0]] - 1) <= 0.05


@pytest.mark.parametrize(
    "samples",
    [
        None,
        [],
        [0] * 399,
        [[0] * 400],
        [float("nan")] * 400,
        [float("inf")] * 400,
        ["0"] * 400,
        [True] * 400,
        [1j] * 400,
    ],
)
def test_stft_invalid_samples(samples):
    with pytest.raises(ValueError):
        calculate_stft(samples, 4000, stft_config())


@pytest.mark.parametrize(
    "rate", [None, 0, -1, float("nan"), float("inf"), "4000", True, 1j]
)
def test_stft_invalid_rate(rate):
    with pytest.raises(ValueError):
        calculate_stft(np.zeros(400), rate, stft_config())


@pytest.mark.parametrize("config", [None, {}, {"window_size": 400}])
def test_stft_missing_config(config):
    with pytest.raises(ValueError, match="CONFIG_REQUIRED"):
        calculate_stft(np.zeros(400), 4000, config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("window_size", 0),
        ("window_size", True),
        ("hop_length", 401),
        ("hop_length", 1.5),
        ("n_fft", 399),
        ("window_function", "not-a-window"),
        ("window_function", [0] * 400),
        ("window_function", [1] * 399),
        ("window_function", [float("nan")] * 400),
    ],
)
def test_stft_invalid_config(field, value):
    config = stft_config()
    config[field] = value
    with pytest.raises(ValueError):
        calculate_stft(np.zeros(400), 4000, config)


@pytest.mark.parametrize("size,hop,n_fft", [(400, 400, 400), (401, 100, 513)])
def test_stft_complete_windows_match_direct_fft(size, hop, n_fft):
    from scipy.signal import get_window

    config = dict(window_size=size, hop_length=hop, n_fft=n_fft, window_function="hann")
    x = np.arange(size + hop + 7, dtype=float)
    result = calculate_stft(x, 4000, config)
    window = get_window("hann", size)
    expected = np.column_stack(
        [
            np.abs(
                np.fft.rfft(x[start : start + size] * window / window.sum(), n=n_fft)
            )
            for start in (0, hop)
        ]
    )
    np.testing.assert_allclose(result.magnitude, expected, atol=1e-12)
    assert result.magnitude.shape == (n_fft // 2 + 1, 2)
    np.testing.assert_allclose(result.times, (size // 2 + np.arange(2) * hop) / 4000)


def test_stft_existing_external_config_and_explicit_window():
    config = STFTConfig(400, 100, 400, "hann", 0.8)
    result = calculate_stft(np.zeros(400), 4000, config)
    assert result.magnitude.shape == (201, 1)
    assert not result.magnitude.any()
    external = stft_config()
    external["window_function"] = np.ones(400)
    result = calculate_stft(np.ones(400), 4000, external)
    assert result.magnitude[0, 0] == pytest.approx(1)


from system.Backend.Server.sensor_runtime import (
    SensorRuntime,
    STFTConfig,
    RealtimeConfig,
)
from system.Backend.Service.cylinder_result_service import CylinderResultService
from system.Sensor.cycle_detector import CycleDetector
from system.ML.WeibullAFT.pipeline import LifecycleConfig, WeibullPipeline


def payload(sequence=1, session=None):
    def chunk(rate):
        values = [0, 10, -10, 20] * 16
        return {
            "sample_rate": rate,
            "sample_format": "s32le",
            "sample_count": len(values),
            "encoding": "base64",
            "data": base64.b64encode(struct.pack("<64i", *values)).decode(),
        }

    return {
        "cylinder_id": "cylinder_01",
        "session_id": session or str(uuid4()),
        "sequence_id": sequence,
        "timestamp": "2026-10-06T12:00:00+09:00",
        "sph0645": chunk(4000),
        "inmp441": chunk(16000),
    }


class Repository:
    def __init__(self):
        self.sessions = {}
        self.raw = []
        self.results = []
        self.fail = False

    def ensure_collection_session(self, row):
        self.sessions[row["session_id"]] = row

    def save_raw_data(self, row):
        if self.fail:
            raise RuntimeError("private connection detail")
        self.raw.append(deepcopy(row))
        return {}

    def save_processed_features_and_ml_result(self, row):
        self.results.append(row)
        return {}

    def save_runtime_metadata(self, identity, metadata, keep=None):
        for row in self.raw:
            if all(
                row[n] == identity[n]
                for n in ("cylinder_id", "session_id", "sequence_id")
            ):
                row["runtime"] = deepcopy(metadata)

    def read_monitoring(self, cylinder, limit):
        rows = [
            dict(
                cylinder_id=p["cylinder_id"],
                session_id=p["session_id"],
                sequence_id=p["sequence_id"],
                measured_at=p["timestamp"],
                runtime=p.get("runtime"),
            )
            for p in self.raw
            if p["cylinder_id"] == cylinder
            and self.sessions[p["session_id"]]["collection_mode"] == "OPERATION"
        ][-limit:]
        preview = next(
            (p for p in reversed(rows) if (p.get("runtime") or {}).get("stft_preview")),
            None,
        )
        return dict(rows=rows, features=None, prediction=None, preview=preview)


def test_synthetic_queue_stft_storage_render_web_contract(monkeypatch):
    """SYNTHETIC fixture only, no Supabase writes or live Web data."""
    import json
    import server
    from system.ML.Condition.analysis import WebPreviewConfig

    repository = Repository()
    rt = SensorRuntime(
        CylinderResultService(repository),
        "OPERATION",
        realtime_config=realtime_limits(),
        stft_config=STFTConfig(32, 16, 32, "hann"),
        preview_config=WebPreviewConfig(4, 3, 10000, 1, 2, 2),
    )
    row = payload()
    assert rt.enqueue_message(json.dumps(row), "smart-cylinder/cylinder_01/sensor")
    assert rt.process_next()
    monkeypatch.setattr(
        CylinderResultService,
        "__init__",
        lambda self: setattr(self, "_repository", repository),
    )
    monkeypatch.setattr(
        server.storage, "exists", lambda serial: serial == "SCC-TEST-0001"
    )
    monkeypatch.setenv("SERIAL_CYLINDER_MAPPING", '{"SCC-TEST-0001":"cylinder_01"}')
    response = server.app.test_client().get(
        "/api/real-cylinder?source=canonical&serial=SCC-TEST-0001"
    )
    assert response.status_code == 200
    latest = response.json["latest"]
    assert latest["live_status"] == "LIVE"
    assert latest["stft_status"] == "COMPLETED"
    assert latest["operation_status"] == "REFERENCE_REQUIRED"
    assert latest["prediction"] is None and latest["vibration_rms"] is None
    assert latest["packet_metrics"]["sph0645"]["rms"] >= 0
    assert latest["packet_metrics"]["inmp441"]["peak"] >= 0
    assert latest["stft_preview"]["sph0645"]["magnitude"]
    assert (
        "raw_payload" not in latest and "data" not in latest and "samples" not in latest
    )
    assert rt.handle_message(row)[1] is True
    assert len(repository.raw) == 1


@pytest.mark.parametrize(
    "received,expected",
    [
        ("2026-10-06T10:00:00+09:00", "LIVE"),
        ("2026-10-06T09:59:49+09:00", "STALE"),
        ("invalid", "NO_DATA"),
        (None, "NO_DATA"),
        ("2026-10-06T10:00:01+09:00", "NO_DATA"),
    ],
)
def test_monitoring_service_stale_and_absence(received, expected):
    from datetime import datetime
    from unittest.mock import MagicMock

    repository = MagicMock()
    repository.read_monitoring.return_value = dict(
        rows=[
            dict(
                session_id=str(uuid4()),
                sequence_id=1,
                measured_at="2026-10-06T09:58:00+09:00",
                runtime=dict(
                    last_received_at=received,
                    stft_status="COMPLETED",
                    operation_status="REFERENCE_REQUIRED",
                    ml_status="WAITING_FOR_OPERATION",
                ),
            )
        ],
        features=None,
        prediction=None,
        preview=None,
    )
    result = CylinderResultService(repository).monitoring(
        "cylinder_01", 1, datetime.fromisoformat("2026-10-06T10:00:00+09:00")
    )
    latest = result["latest"]
    assert latest["live_status"] == expected
    assert latest["prediction"] is None and latest["vibration_rms"] is None
    assert latest["operation_status"] == "REFERENCE_REQUIRED"
    assert "raw_payload" not in latest


def test_preview_reduction_limits_and_original_preservation():
    import json
    from system.ML.Condition.analysis import WebPreviewConfig, reduce_stft_preview

    result = calculate_stft(np.arange(4000), 4000, stft_config())
    original = result.magnitude.copy()
    snapshot = dict(sensors={s: dict(result=result) for s in ("sph0645", "inmp441")})
    config = WebPreviewConfig(8, 5, 20000, 1, 2, 3)
    preview = reduce_stft_preview(snapshot, config)
    assert len(preview["sph0645"]["magnitude"]) == 8
    assert len(preview["sph0645"]["relative_times"]) == 5
    assert len(json.dumps(preview, separators=(",", ":")).encode()) <= config.max_bytes
    np.testing.assert_array_equal(result.magnitude, original)
    with pytest.raises(ValueError, match="PREVIEW_TOO_LARGE"):
        reduce_stft_preview(snapshot, WebPreviewConfig(8, 5, 1, 1, 2, 3))


def test_runtime_receive_time_preview_interval_and_sender_isolation():
    import json
    from system.ML.Condition.analysis import WebPreviewConfig

    now = [0]
    repo = Repository()
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TEST",
        realtime_config=realtime_limits(),
        stft_config=STFTConfig(32, 16, 32, "hann"),
        clock=lambda: now[0],
        wall_clock=lambda: "2026-10-06T10:00:00+09:00",
        preview_config=WebPreviewConfig(4, 3, 10000, 1, 2, 2),
    )
    row = payload()
    row["runtime"] = {"ml_status": "NORMAL"}
    assert rt.enqueue_message(json.dumps(row))
    now[0] = 0.5
    rt.process_next()
    stored = repo.raw[-1]
    assert stored["timestamp"] == row["timestamp"]
    assert stored["runtime"]["last_received_at"] == "2026-10-06T10:00:00+09:00"
    assert stored["runtime"]["ml_status"] == "MODEL_REQUIRED"
    assert stored["runtime"]["stft_status"] == "COMPLETED"
    assert stored["runtime"]["stft_preview"]
    row["sequence_id"] = 2
    rt.enqueue_message(json.dumps(row))
    rt.process_next()
    assert repo.raw[-1]["runtime"]["stft_preview"] is None
    assert repo.raw[-1]["sph0645"]["data"] == row["sph0645"]["data"]


def test_training_loader_never_reads_raw_or_runtime():
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository

    client = MagicMock()
    query = client.table.return_value
    for method in ("select", "eq", "in_"):
        getattr(query, method).return_value = query
    query.execute.side_effect = [
        SimpleNamespace(
            data=[
                dict(
                    session_id="session",
                    dataset_type="TRAINING_NORMAL",
                    ground_truth="NORMAL",
                )
            ]
        ),
        SimpleNamespace(data=[dict(session_id="session", sph0645_rms=1)]),
    ]
    dataset = CylinderResultRepository(client).load_training_dataset()
    assert dataset[0]["sph0645_rms"] == 1
    assert "runtime" not in dataset[0]
    assert [c.args[0] for c in client.table.call_args_list] == [
        "collection_sessions",
        "processed_features",
    ]


def test_render_canonical_response_uses_service(monkeypatch):
    import server

    result = dict(
        source="canonical",
        cylinder_id="cylinder_02",
        count=0,
        latest=None,
        history=[],
        live_status="NO_DATA",
    )
    monkeypatch.setattr(
        CylinderResultService,
        "monitoring",
        lambda self, cylinder, limit: result if cylinder == "cylinder_02" else None,
    )
    monkeypatch.setattr(
        server.storage, "exists", lambda serial: serial == "SCC-TEST-0002"
    )
    monkeypatch.setenv("SERIAL_CYLINDER_MAPPING", '{"SCC-TEST-0002":"cylinder_02"}')
    response = server.app.test_client().get(
        "/api/real-cylinder?source=canonical&serial=SCC-TEST-0002&cylinder_id=cylinder_01"
    )
    assert response.status_code == 200
    assert response.json == dict(result, serial="SCC-TEST-0002")


@pytest.mark.parametrize(
    "serial,mapping,status",
    [
        ("SCC-TEST-0001", "{}", 409),
        ("SCC-TEST-0001", "invalid", 503),
        ("SCC-TEST-0001", '{"SCC-TEST-0001":"cylinder_99"}', 503),
        ("SCC-TEST-0002", '{"SCC-TEST-0002":"cylinder_02"}', 403),
        ("", "{}", 403),
    ],
)
def test_render_serial_fails_closed(monkeypatch, serial, mapping, status):
    import server

    monkeypatch.setattr(server.storage, "exists", lambda s: s == "SCC-TEST-0001")
    monkeypatch.setenv("SERIAL_CYLINDER_MAPPING", mapping)
    monkeypatch.setattr(
        CylinderResultService,
        "monitoring",
        lambda *args: pytest.fail("unauthorized repository read"),
    )
    response = server.app.test_client().get(
        "/api/real-cylinder?source=canonical&serial=" + serial
    )
    assert response.status_code == status
    assert response.json["latest"] is None and response.json["history"] == []


def test_monitoring_javascript_states_and_polling():
    import subprocess
    from pathlib import Path

    script = Path(__file__).parents[1] / "public" / "real-monitor.js"
    harness = r"""
const vm=require('vm'), fs=require('fs'), assert=require('assert');
const elements={};
const ctx=new Proxy({}, {get:(o,k)=>o[k] || (()=>{})});
const box={console, URLSearchParams, Date, Number, Math, JSON, NaN,
 location:{hostname:'example.com', search:'?serial=SCC-TEST-0001', origin:'https://example.com'},
 window:{location:{origin:'https://example.com'}}, devicePixelRatio:1,
 document:{getElementById:id=>elements[id] ||= {textContent:'',className:'',clientWidth:500,clientHeight:300,getContext:()=>ctx}},
 addEventListener:()=>{},setInterval:(fn,n)=>{assert.equal(n,2000);},
 fetch:async url=>{assert(url.includes('source=canonical')); assert(url.includes('serial=SCC-TEST-0001'));return {ok:true,json:async()=>({latest:null})};}};
vm.createContext(box);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),box);
vm.runInContext('renderCanonical({latest:null})',box);
assert.equal(elements.connection.textContent,'NO LIVE DATA');
assert.equal(elements.prediction.textContent,'NO PREDICTION');
assert.equal(elements.sphRms.textContent,'WAITING FOR SENSOR');
box.latest={timestamp:new Date().toISOString(),last_received_at:new Date().toISOString(),live_status:'LIVE',stft_status:'COMPLETED',operation_status:'REFERENCE_REQUIRED',ml_status:'MODEL_REQUIRED'};
vm.runInContext('renderCanonical({latest,cylinder_id:"cylinder_01"})',box);
assert(elements.connection.textContent.startsWith('LIVE'));
assert.equal(elements.motion.textContent,'REFERENCE_REQUIRED');
assert.equal(elements.stftState.textContent,'COMPLETED');
box.latest.live_status='STALE';box.latest.last_received_at=new Date(Date.now()-11000).toISOString();
vm.runInContext('renderCanonical({latest})',box);
assert(elements.connection.textContent.startsWith('STALE'));
box.charts={};
vm.runInContext('draw=(canvas,lines)=>{ charts[canvas === document.getElementById("packetChart") ? "packet" : "cycle"] = lines; }',box);
box.latest={session_id:'s1',timestamp:new Date().toISOString(),last_received_at:new Date().toISOString(),live_status:'LIVE',packet_metrics:{sph0645:{rms:0,peak:0},inmp441:{rms:12,peak:24}}};
box.history=[{session_id:'old',sequence_id:1,packet_metrics:{sph0645:{rms:999}}},{session_id:'s1',sequence_id:1,packet_metrics:{sph0645:{rms:0}}},{session_id:'s1',sequence_id:3,packet_metrics:{sph0645:{rms:10}}}];
vm.runInContext('renderCanonical({latest,history})',box);
assert.equal(elements.sphRms.textContent,0);
assert.equal(elements.inmpPeak.textContent,24);
assert.deepEqual(Array.from(box.charts.packet[0].values),[0,null,10]);
assert.equal(elements.rul.textContent,'수명 데이터 부족');
assert.equal(box.charts.cycle[0].values.length,0);
vm.runInContext('updateConnectionStatus("invalid")',box);
assert.equal(elements.connection.textContent,'NO LIVE DATA');
box.preview={relative_times:[0.1,0.2],frequencies:[0,100],magnitude:[[0,1],[1,2]]};
box.latest.live_status='NO_DATA';box.latest.last_received_at=new Date().toISOString();
vm.runInContext('renderCanonical({latest})',box);
assert.equal(elements.connection.textContent,'NO LIVE DATA');
vm.runInContext('drawSpectrogram(document.getElementById("sph0645Preview"),preview)',box);
vm.runInContext('drawSpectrogram(document.getElementById("sph0645Preview"),null)',box);
"""
    subprocess.run(
        ["node", "-e", harness, str(script)], check=True, capture_output=True, text=True
    )


class STFTInterface:
    def detect(self, chunks, config):
        assert config.n_fft == 32
        return (0, 0.004)


def runtime(repo, configured=True, condition=None):
    kwargs = {}
    if configured:
        if condition is None:
            from system.ML.Condition.analysis import PREPROCESSING_VERSION

            condition = lambda features, model: {"ready": True, "prediction": "NORMAL"}
            condition.preprocessing_version = PREPROCESSING_VERSION
        kwargs = {
            "detector_factory": lambda cylinder: CycleDetector(
                lambda distances: distances, 1, 100, lambda: 0
            ),
            "max_cycle_chunks": 10,
            "stft_config": STFTConfig(32, 16, 32, "hann", 0.8),
            "stft_detector": STFTInterface(),
            "condition_predictor": condition,
        }
    return SensorRuntime(
        CylinderResultService(repo), "TEST", realtime_config=realtime_limits(), **kwargs
    )


def realtime_limits(**changes):
    values = dict(
        queue_max_size=2,
        max_payload_bytes=20000,
        dedup_max_sessions=12,
        dedup_ttl_seconds=30,
        sph0645_max_samples=128,
        inmp441_max_samples=128,
        buffer_max_age_seconds=10,
        queue_max_age_seconds=10,
        shutdown_timeout_seconds=2,
    )
    values.update(changes)
    return RealtimeConfig(**values)


@pytest.mark.parametrize(
    "cylinder,dataset,truth",
    [
        ("cylinder_01", "TRAINING_NORMAL", "NORMAL"),
        ("cylinder_02", "TRAINING_ABNORMAL", "SEAL_LEAK"),
    ],
)
def test_raw_only_training_preserves_both_sensors_without_analysis(
    cylinder, dataset, truth
):
    import json

    repo = Repository()
    now = [0]
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TRAINING",
        realtime_config=realtime_limits(),
        raw_collection_only=True,
        clock=lambda: now[0],
    )
    row = payload()
    row["cylinder_id"] = cylinder
    assert rt.enqueue_message(json.dumps(row), "smart-cylinder/%s/sensor" % cylinder)
    now[0] = 100  # Raw must not expire with the analysis freshness limit.
    assert rt.process_next()
    assert len(repo.raw) == 1
    session = repo.sessions[row["session_id"]]
    assert session["dataset_type"] == dataset
    assert session["ground_truth"] == truth
    assert session["ground_truth_source"] == "MANUAL_EXPERIMENT"
    for name in ("sph0645", "inmp441"):
        assert repo.raw[0][name]["data"] == row[name]["data"]
    assert repo.raw[0]["runtime"]["stft_status"] == "CONFIG_REQUIRED"
    assert not rt._buffers and not rt.latest_stft and not repo.results
    assert rt.status == "RAW_COLLECTION_RUNNING"
    assert rt.enqueue_message(json.dumps(row))
    rt.process_next()
    assert len(repo.raw) == 1


def test_raw_only_adapter_accepts_missing_stft_and_preview(monkeypatch):
    import importlib.util
    import json
    from pathlib import Path
    import system

    path = Path(system.__file__).parents[1] / "ML-cylinder/deploy/pi_sensor_runtime.py"
    spec = importlib.util.spec_from_file_location("raw_adapter_test", path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    for key in ("SENSOR_STFT_CONFIG", "SENSOR_PREVIEW_CONFIG"):
        monkeypatch.delenv(key, raising=False)
    for key in ("MQTT_PASSWORD", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.setenv(key, "synthetic-test-value")
    monkeypatch.setenv("COLLECTION_MODE", "TRAINING")
    monkeypatch.setenv("TRAINING_CYLINDER_ID", "cylinder_01")
    monkeypatch.setenv("TRAINING_GROUND_TRUTH", "NORMAL")
    monkeypatch.setenv("COLLECTION_EXPERIMENT_ID", "test-normal")
    monkeypatch.setenv("SENSOR_REALTIME_CONFIG", json.dumps(vars(realtime_limits())))
    rt = adapter.create_runtime()
    assert rt.raw_collection_only and rt.stft_config is None
    assert rt.preview_config is None
    assert not rt._buffers


@pytest.mark.parametrize(
    "truth,dataset", [("NORMAL", "TRAINING_NORMAL"), ("SEAL_LEAK", "TRAINING_ABNORMAL")]
)
def test_manual_training_label_on_same_pico(truth, dataset):
    from unittest.mock import MagicMock
    from system.Backend.Service.cylinder_result_service import CylinderResultService

    repo = MagicMock()
    service = CylinderResultService(
        repo,
        training_cylinder_id="cylinder_01",
        training_ground_truth=truth,
        experiment_id="manual-experiment",
    )
    row = payload()
    row["sequence_id"] = 1
    service.record_sensor_message(row, collection_mode="TRAINING")
    session = repo.ensure_collection_session.call_args.args[0]
    assert session["dataset_type"] == dataset
    assert session["ground_truth"] == truth
    assert session["experiment_id"] == "manual-experiment"
    assert repo.ensure_collection_session.call_args.kwargs == {"reject_existing": True}
    row["sequence_id"] = 2
    service.record_sensor_message(row, collection_mode="TRAINING")
    assert repo.ensure_collection_session.call_args.kwargs == {}


def test_manual_training_rejects_old_stream_wrong_device_and_session_switch():
    from unittest.mock import MagicMock
    from system.Backend.Service.cylinder_result_service import (
        CylinderResultService,
        CollectionModeError,
    )

    repo = MagicMock()
    service = CylinderResultService(
        repo,
        training_cylinder_id="cylinder_01",
        training_ground_truth="SEAL_LEAK",
        experiment_id="fault-01",
    )
    row = payload()
    row["sequence_id"] = 2
    with pytest.raises(CollectionModeError, match="NEW_SESSION_REQUIRED"):
        service.record_sensor_message(row, collection_mode="TRAINING")
    row["sequence_id"] = 1
    row["cylinder_id"] = "cylinder_02"
    with pytest.raises(CollectionModeError, match="UNSELECTED"):
        service.record_sensor_message(row, collection_mode="TRAINING")
    repo.save_raw_data.assert_not_called()
    row["cylinder_id"] = "cylinder_01"
    service.record_sensor_message(row, collection_mode="TRAINING")
    row["session_id"] = str(uuid4())
    with pytest.raises(CollectionModeError, match="NEW_RUN_REQUIRED"):
        service.record_sensor_message(row, collection_mode="TRAINING")


def test_new_training_session_uses_insert_never_upsert():
    from unittest.mock import MagicMock
    from system.DB.Supabase.cylinder_result_repository import (
        CylinderResultRepository,
        RepositoryWriteError,
    )

    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = RuntimeError(
        "existing session"
    )
    session = dict(
        session_id=str(uuid4()),
        cylinder_id="cylinder_01",
        collection_mode="TRAINING",
        dataset_type="TRAINING_ABNORMAL",
        ground_truth="SEAL_LEAK",
        ground_truth_source="MANUAL_EXPERIMENT",
        experiment_id="fault-01",
        started_at="2026-10-07T14:00:00+09:00",
    )
    with pytest.raises(RepositoryWriteError):
        CylinderResultRepository(client).ensure_collection_session(
            session, reject_existing=True
        )
    client.table.return_value.upsert.assert_not_called()


def test_interactive_launcher_requires_confirmation_and_sets_label(monkeypatch):
    import runpy
    from pathlib import Path
    import system

    adapter = runpy.run_path(
        str(
            Path(system.__file__).parents[1] / "ML-cylinder/deploy/pi_sensor_runtime.py"
        )
    )
    values = iter(["pico01", "fault-01", "2", "YES"])
    adapter["configure_interactive"](lambda _: next(values))
    assert __import__("os").environ["TRAINING_GROUND_TRUTH"] == "SEAL_LEAK"
    assert __import__("os").environ["TRAINING_CYLINDER_ID"] == "cylinder_01"
    values = iter(["pico01", "fault-02", "1", "NO"])
    with pytest.raises(ValueError, match="confirmed"):
        adapter["configure_interactive"](lambda _: next(values))
    # Tests must not leak interactive selections into following tests.
    for key in (
        "TRAINING_CYLINDER_ID",
        "TRAINING_GROUND_TRUTH",
        "COLLECTION_EXPERIMENT_ID",
        "COLLECTION_MODE",
        "SENSOR_REALTIME_CONFIG",
    ):
        monkeypatch.delenv(key, raising=False)


def test_raw_only_failed_store_remains_retryable(caplog):
    repo = Repository()
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TRAINING",
        realtime_config=realtime_limits(),
        raw_collection_only=True,
    )
    row = payload()
    repo.fail = True
    assert rt.handle_message(row) is None
    assert not repo.raw
    assert rt.metrics["RAW_STORAGE_ERROR"] == 1
    assert "PERSISTENCE_GAP" in caplog.text
    assert row["session_id"] in caplog.text
    assert "private connection detail" not in caplog.text
    repo.fail = False
    assert rt.handle_message(row)
    assert len(repo.raw) == 1


def test_raw_only_shutdown_drains_accepted_packets():
    import json

    repo = Repository()
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TRAINING",
        realtime_config=realtime_limits(),
        raw_collection_only=True,
    )
    row = payload()
    assert rt.enqueue_message(json.dumps(row))
    row["sequence_id"] = 2
    assert rt.enqueue_message(json.dumps(row))
    assert rt.start()
    assert rt.stop()
    assert [r["sequence_id"] for r in repo.raw] == [1, 2]
    assert rt.analysis_queue.unfinished_tasks == 0
    assert not rt.metrics.get("SHUTDOWN_DROPPED")


def test_queue_bounded_fifo_and_overflow(caplog):
    import json

    repo = Repository()
    rt = runtime(repo, False)
    row = payload()
    assert rt.enqueue_message(json.dumps(row))
    row["sequence_id"] = 2
    assert rt.enqueue_message(json.dumps(row))
    assert not rt.enqueue_message(json.dumps(row))
    assert rt.analysis_queue.qsize() == 2
    assert not repo.raw
    assert rt.metrics["QUEUE_OVERFLOW_DROPPED"] == 1
    assert "QUEUE_OVERFLOW_DROPPED" in caplog.text
    assert rt.process_next() and rt.process_next()
    assert [r["sequence_id"] for r in repo.raw] == [1, 2]


def test_mqtt_callback_uses_queue_with_topic():
    import json
    from types import SimpleNamespace
    from system.MQTT.client import PiMqttClient

    repo = Repository()
    rt = runtime(repo, False)
    client = PiMqttClient(None, subscriber=rt)
    message = SimpleNamespace(
        payload=json.dumps(payload()).encode(),
        topic="smart-cylinder/cylinder_01/sensor",
    )
    assert client._paho_on_message(None, None, message)
    assert not repo.raw
    assert rt.process_next()
    assert len(repo.raw) == 1


def test_dedup_session_limit_and_high_water_mark():
    from system.Backend.Server.sensor_runtime import StorageDuplicateTracker
    from system.MQTT.message_parser import parse_payload

    events = []
    tracker = StorageDuplicateTracker(
        realtime_limits(dedup_max_sessions=2),
        lambda: 0,
        lambda *args: events.append(args),
    )
    for _ in range(3):
        row = parse_payload(payload(100))
        assert not tracker.record(row)
        tracker.acknowledge(row)
    assert len(tracker._messages) == 2
    assert events == [("DEDUP_SESSION_EVICTED", True)]
    assert tracker.record(row)
    row["sequence_id"] = 99
    assert tracker.record(row)
    row["sequence_id"] = 101
    assert not tracker.record(row)
    assert all(isinstance(v[0], int) for v in tracker._messages.values())


def test_dedup_ttl_cleanup():
    from system.Backend.Server.sensor_runtime import StorageDuplicateTracker
    from system.MQTT.message_parser import parse_payload

    now = [0]
    events = []
    tracker = StorageDuplicateTracker(
        realtime_limits(), lambda: now[0], lambda *args: events.append(args)
    )
    row = parse_payload(payload())
    tracker.acknowledge(row)
    now[0] = 31
    tracker.cleanup()
    assert not tracker._messages
    assert tracker.record(row)
    assert events == [("DEDUP_SESSION_EXPIRED",)]


def test_retired_session_cannot_replace_current_session():
    repo = Repository()
    rt = runtime(repo, False)
    old = payload()
    rt.handle_message(old)
    current = payload()
    rt.handle_message(current)
    old["sequence_id"] = 2
    rt.handle_message(old)
    assert len(repo.raw) == 2
    assert rt._buffers["cylinder_01"].session == current["session_id"]


def test_six_cylinders_have_separate_bounded_stft_snapshots():
    repo = Repository()
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TEST",
        realtime_config=realtime_limits(),
        stft_config={
            "sph0645": STFTConfig(32, 16, 64, "hann"),
            "inmp441": STFTConfig(64, 32, 128, "hann"),
        },
    )
    for cylinder in range(1, 7):
        row = payload()
        row["cylinder_id"] = "cylinder_%02d" % cylinder
        rt.handle_message(row)
    assert len(rt._buffers) == len(rt.latest_stft) == len(rt._tracker._active) == 6
    assert len(repo.raw) == 6
    for snapshot in rt.latest_stft.values():
        assert snapshot["sensors"]["sph0645"]["result"].magnitude.shape == (33, 3)
        assert snapshot["sensors"]["inmp441"]["result"].magnitude.shape == (65, 1)


def test_shutdown_drops_pending_and_reports_stop_timeout():
    import json
    import threading

    repo = Repository()
    entered = threading.Event()
    release = threading.Event()
    original = repo.save_raw_data

    def blocked(row):
        entered.set()
        assert release.wait(3)
        return original(row)

    repo.save_raw_data = blocked
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TEST",
        realtime_config=realtime_limits(shutdown_timeout_seconds=0.01),
    )
    row = payload()
    rt.start()
    try:
        rt.enqueue_message(json.dumps(row))
        assert entered.wait(3)
        row["sequence_id"] = 2
        rt.enqueue_message(json.dumps(row))
        assert not rt.stop()
        assert not rt.enqueue_message(json.dumps(row))
        assert not rt.start()
        assert rt.metrics["WORKER_STOP_TIMEOUT"] == 1
    finally:
        release.set()
        rt._worker.join(3)
        assert rt.stop()
    assert rt.analysis_queue.unfinished_tasks == 0
    assert rt.metrics["SHUTDOWN_DROPPED"] == 1


def test_buffer_bounded_order_gap_session_and_expiry():
    now = [0]
    rt = runtime(Repository(), False)
    rt.clock = lambda: now[0]
    row = payload()
    for seq in (1, 2, 3):
        row["sequence_id"] = seq
        rt.handle_message(row)
    buffer = rt._buffers["cylinder_01"]
    assert len(buffer.samples["sph0645"]) == len(buffer.samples["inmp441"]) == 128
    assert buffer.start_indexes["sph0645"] == 64
    assert list(buffer.samples["sph0645"]) == [0, 10, -10, 20] * 32
    row["sequence_id"] = 5
    rt.handle_message(row)
    assert rt.metrics["BUFFER_SEQUENCE_GAP"] == 1
    assert len(rt._buffers["cylinder_01"].samples["sph0645"]) == 64
    rt.handle_message(payload())
    assert rt.metrics["BUFFER_SESSION_CHANGED"] == 1
    now[0] = 11
    rt.cleanup()
    assert not rt._buffers
    assert rt.buffer_status["cylinder_01"] == "BUFFER_EXPIRED"


def test_buffer_ignores_old_sequence_and_timestamp():
    repo = Repository()
    rt = runtime(repo, False)
    row = payload(2)
    rt.handle_message(row)
    row["sequence_id"] = 1
    rt.handle_message(row)
    assert len(repo.raw) == 1
    row["sequence_id"] = 3
    row["timestamp"] = "2026-10-06T11:59:59+09:00"
    rt.handle_message(row)
    assert len(repo.raw) == 1
    assert rt.metrics["OUT_OF_ORDER_TIMESTAMP_DROPPED"] == 1


def test_buffer_to_canonical_stft_without_fake_reference():
    import json

    repo = Repository()
    received = []
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TEST",
        realtime_config=realtime_limits(),
        stft_config=STFTConfig(128, 32, 128, "hann"),
        stft_callback=received.append,
    )
    row = payload()
    assert rt.enqueue_message(json.dumps(row))
    rt.process_next()
    assert rt.status == "WAITING_SAMPLES"
    row["sequence_id"] = 2
    rt.enqueue_message(json.dumps(row))
    rt.process_next()
    assert rt.status == "REFERENCE_REQUIRED"
    snapshot = rt.latest_stft["cylinder_01"]
    assert snapshot["timestamp"] == row["timestamp"]
    assert snapshot["sequence_id"] == 2
    assert snapshot["operation_status"] == "REFERENCE_REQUIRED"
    for sensor, rate in (("sph0645", 4000), ("inmp441", 16000)):
        result = snapshot["sensors"][sensor]["result"]
        assert result.magnitude.shape == (65, 1)
        assert result.times[0] == pytest.approx(64 / rate)
    assert received == [snapshot]
    assert not repo.results


def test_synthetic_wire_queue_buffer_stft_100hz_and_expiry():
    """SYNTHETIC INTEGRATION TEST; no broker, hardware or database writes."""
    import json

    now = [0]
    repo = Repository()
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TEST",
        clock=lambda: now[0],
        realtime_config=realtime_limits(
            sph0645_max_samples=400, inmp441_max_samples=1600
        ),
        stft_config={
            "sph0645": STFTConfig(400, 100, 400, "hann"),
            "inmp441": STFTConfig(1600, 400, 1600, "hann"),
        },
    )
    row = payload()
    for sequence in range(1, 5):
        row["sequence_id"] = sequence
        for sensor, rate, count in (("sph0645", 4000, 100), ("inmp441", 16000, 400)):
            indexes = np.arange((sequence - 1) * count, sequence * count)
            pcm = np.rint(1_000_000 * np.sin(2 * np.pi * 100 * indexes / rate)).astype(
                "<i4"
            )
            row[sensor].update(
                sample_count=count, data=base64.b64encode(pcm.tobytes()).decode()
            )
        assert rt.enqueue_message(json.dumps(row), "smart-cylinder/cylinder_01/sensor")
        assert rt.process_next()
    assert len(repo.raw) == 4
    snapshot = rt.latest_stft["cylinder_01"]
    for sensor in ("sph0645", "inmp441"):
        result = snapshot["sensors"][sensor]["result"]
        assert result.frequencies[result.magnitude[:, 0].argmax()] == 100
        assert result.times[0] == pytest.approx(0.05)
    assert not repo.results
    now[0] = 11
    rt.cleanup()
    assert not rt.latest_stft and not rt._buffers


def test_stft_error_isolated_and_next_packet_can_run(caplog):
    import json

    rt = SensorRuntime(
        CylinderResultService(Repository()),
        "TEST",
        realtime_config=realtime_limits(),
        stft_config=STFTConfig(32, 16, 32, "invalid-window"),
    )
    row = payload()
    rt.enqueue_message(json.dumps(row))
    rt.process_next()
    assert rt.status == "ANALYSIS_ERROR"
    assert rt.last_error == "ValueError"
    assert "ANALYSIS_ERROR" in caplog.text
    assert not rt.latest_stft
    rt.stft_config = STFTConfig(32, 16, 32, "hann")
    row["sequence_id"] = 2
    rt.enqueue_message(json.dumps(row))
    rt.process_next()
    assert rt.status == "REFERENCE_REQUIRED"
    assert rt.latest_stft["cylinder_01"]["sequence_id"] == 2


def test_worker_receives_while_storage_is_blocked():
    import json
    import threading

    repo = Repository()
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    original = repo.save_raw_data

    def blocked(row):
        entered.set()
        assert release.wait(3)
        return original(row)

    repo.save_raw_data = blocked
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TEST",
        realtime_config=realtime_limits(),
        stft_config=STFTConfig(32, 16, 32, "hann"),
        stft_callback=lambda snapshot: completed.set(),
    )
    row = payload()
    assert rt.start()
    try:
        rt.enqueue_message(json.dumps(row))
        assert entered.wait(3)
        row["sequence_id"] = 2
        assert rt.enqueue_message(json.dumps(row))
        assert rt.analysis_queue.qsize() == 1
        assert not repo.raw
        release.set()
        assert completed.wait(3)
    finally:
        release.set()
        assert rt.stop()
    assert not rt._worker.is_alive()
    assert rt.analysis_queue.unfinished_tasks == 0


def test_worker_automatic_stft_and_callback_error_recovery():
    import json
    import threading

    done = threading.Event()
    recovered = threading.Event()
    calls = []

    def next_stage(snapshot):
        calls.append(snapshot["sequence_id"])
        if len(calls) == 1:
            done.set()
            raise RuntimeError("must not log this private detail")
        recovered.set()

    repo = Repository()
    rt = SensorRuntime(
        CylinderResultService(repo),
        "TEST",
        realtime_config=realtime_limits(),
        stft_config=STFTConfig(32, 16, 32, "hann"),
        stft_callback=next_stage,
    )
    row = payload()
    rt.start()
    try:
        rt.enqueue_message(json.dumps(row))
        assert done.wait(3)
        row["sequence_id"] = 2
        rt.enqueue_message(json.dumps(row))
        assert recovered.wait(3)
        assert rt._worker.is_alive()
        assert calls == [1, 2]
        assert rt.metrics["ANALYSIS_ERROR"] == 1
        assert len(repo.raw) == 2
        assert not repo.results
    finally:
        assert rt.stop()


@pytest.mark.parametrize(
    "wire,topic,event",
    [
        (b"x" * 20001, None, "PAYLOAD_TOO_LARGE"),
        ({}, None, "INVALID_WIRE_PAYLOAD"),
        ("\ud800", None, "INVALID_WIRE_PAYLOAD"),
        (b"{}", "smart-cylinder/cylinder_99/sensor", "INVALID_TOPIC"),
    ],
)
def test_queue_rejects_bad_wire_inputs(wire, topic, event):
    rt = runtime(Repository(), False)
    assert not rt.enqueue_message(wire, topic)
    assert rt.metrics[event] == 1
    assert rt.analysis_queue.qsize() == 0


def test_queue_stale_drop_and_parse_error_recovery():
    import json

    now = [0]
    repo = Repository()
    rt = runtime(repo, False)
    rt.clock = lambda: now[0]
    row = payload()
    rt.enqueue_message(json.dumps(row))
    now[0] = 11
    rt.process_next()
    assert not repo.raw
    assert rt.metrics["QUEUE_STALE_DROPPED"] == 1
    rt.enqueue_message(b"not JSON")
    rt.process_next()
    assert rt.metrics["INPUT_OR_STORAGE_FAILED"] == 1
    rt.enqueue_message(json.dumps(row))
    rt.process_next()
    assert len(repo.raw) == 1


@pytest.mark.parametrize(
    "config", [None, {}, {"window_size": 32}, STFTConfig(129, 32, 129, "hann")]
)
def test_runtime_stft_config_required(config):
    rt = SensorRuntime(
        CylinderResultService(Repository()),
        "TEST",
        realtime_config=realtime_limits(),
        stft_config=config,
    )
    rt.handle_message(payload())
    assert rt.status == "CONFIG_REQUIRED"
    assert not rt.latest_stft


@pytest.mark.parametrize(
    "field,value",
    [
        ("queue_max_size", 0),
        ("max_payload_bytes", True),
        ("dedup_max_sessions", 0),
        ("dedup_ttl_seconds", float("nan")),
        ("sph0645_max_samples", 0),
        ("buffer_max_age_seconds", -1),
        ("queue_max_age_seconds", "10"),
        ("shutdown_timeout_seconds", float("inf")),
    ],
)
def test_realtime_limits_require_valid_external_config(field, value):
    with pytest.raises(ValueError, match="CONFIG_REQUIRED"):
        realtime_limits(**{field: value})


def complete(rt, row):
    rt.handle_message(row)
    rt.update_distances("cylinder_01", "FORWARD")
    row = deepcopy(row)
    row["sequence_id"] += 1
    rt.handle_message(row)
    rt.update_distances("cylinder_01", "RETURN")
    rt.update_distances("cylinder_01", "STOP")


def test_parser_dedup_session_raw():
    repo = Repository()
    rt = runtime(repo, False)
    row = payload()
    assert rt.handle_message(row)[1] is False
    assert rt.handle_message(row)[1] is True
    assert len(repo.raw) == 1
    assert repo.sessions[row["session_id"]]["dataset_type"] == "TEST"
    assert repo.sessions[row["session_id"]]["ground_truth"] is None
    assert rt.status == "CONFIG_REQUIRED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", "bad"),
        ("sequence_id", 0),
        ("timestamp", "2026-10-06T12:00:00"),
        ("cylinder_id", "bad"),
    ],
)
def test_invalid_payload_isolated(field, value):
    repo = Repository()
    rt = runtime(repo)
    row = payload()
    row[field] = value
    assert rt.handle_message(row) is None
    assert repo.raw == []


def test_storage_failure_can_retry_and_does_not_expose_error():
    repo = Repository()
    rt = runtime(repo)
    row = payload()
    repo.fail = True
    assert rt.handle_message(row) is None
    assert rt.last_error == "RuntimeError"
    repo.fail = False
    assert rt.handle_message(row)[1] is False
    assert len(repo.raw) == 1


def test_cycle_stft_features_condition_service_repository():
    repo = Repository()
    rt = runtime(repo)
    complete(rt, payload())
    assert rt.status == "STORED"
    result = repo.results[0]
    assert result["features"]["sph0645_rms"] > 0
    assert result["features"]["fft_features"]["sph0645"]["sample_rate"] == 4000
    assert result["prediction"] == "NORMAL"
    assert result["cycle_id"]


def test_model_unavailable_does_not_create_prediction():
    repo = Repository()
    rt = runtime(repo, condition=lambda features, model: {"ready": False})
    complete(rt, payload())
    assert rt.status == "MODEL_REQUIRED"
    assert not repo.results


def test_topic_identity_mismatch():
    rt = runtime(Repository())
    assert rt.handle_message(payload(), "smart-cylinder/cylinder_02/sensor") is None


def test_new_session_discards_old_cycle():
    repo = Repository()
    rt = runtime(repo)
    row = payload()
    rt.handle_message(row)
    rt.update_distances("cylinder_01", "FORWARD")
    rt.handle_message(payload())
    rt.update_distances("cylinder_01", "RETURN")
    rt.update_distances("cylinder_01", "STOP")
    assert not repo.results


def life_pipeline():
    return WeibullPipeline(
        LifecycleConfig(("rms",), "subject", "duration", "event", 0.25, 42, 4, 2, {})
    )


def test_no_lifecycle_data():
    pipe = life_pipeline()
    assert pipe.train_from_repository(lambda: [])["status"] == "DATA_REQUIRED"
    assert pipe.predict([{"rms": 1}])["status"] == "TRAINING_NOT_AVAILABLE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("duration", 0),
        ("duration", float("nan")),
        ("event", 2),
        ("rms", float("inf")),
    ],
)
def test_invalid_lifecycle(field, value):
    row = {"subject": "real-subject-contract", "duration": 10, "event": 1, "rms": 1}
    row[field] = value
    with pytest.raises(ValueError):
        life_pipeline().validate([row])


def test_censored_data_not_enough_to_train():
    rows = [
        {"subject": str(i), "duration": i + 1, "event": 0, "rms": 1} for i in range(10)
    ]
    assert life_pipeline().train(rows)["status"] == "TRAINING_NOT_AVAILABLE"


@pytest.mark.parametrize(
    "requested,detected,expected",
    [
        ("A", "A", "OK"),
        ("A", "B", "NG"),
        ("B", "B", "OK"),
        ("B", "A", "NG"),
    ],
)
def test_order_comparison_regression(requested, detected, expected):
    import server

    assert server.compare_process_products(requested, detected) == expected


@pytest.mark.parametrize(
    "target",
    [
        "Pi deployment",
        "Pico microphones",
        "Mosquitto",
        "STFT performance",
        "Condition performance",
        "DataWorX",
        "PLC",
    ],
)
def test_hardware_required(target):
    pytest.skip("HARDWARE REQUIRED / NOT TESTED: " + target)


def test_real_weibull_training_requires_data():
    pytest.skip("DATA REQUIRED: actual lifecycle observations unavailable")
