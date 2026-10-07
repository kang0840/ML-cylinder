"""Source and installed-wheel contracts; no live DB, broker, or hardware access."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RENDER_ROOT = PROJECT_ROOT / "ML-cylinder"
WHEEL = RENDER_ROOT / "deploy" / "smart_cylinder_common-0.1.8-py3-none-any.whl"
WHEEL_SHA256 = "71a3a137125a7b603d3d86f6533fbe1c4c2b05194c53c899bb75e5133c4eeec4"
MODULES = (
    "system",
    "system.ML.Condition.analysis",
    "system.Backend.Service.cylinder_result_service",
    "system.DB.Supabase.cylinder_result_repository",
    "system.Backend.API.cylinder_result",
    "system.Backend.Server.sensor_runtime",
    "system.DataWorX.process_order_bridge",
)
PROBE = r"""
import hashlib, importlib, importlib.metadata, json, sysconfig
names = %s
result = {
    "version": importlib.metadata.version("smart-cylinder-common"),
    "site": sysconfig.get_path("purelib"),
    "modules": {},
}
for name in names:
    module = importlib.import_module(name)
    result["modules"][name] = {
        "path": module.__file__,
        "sha256": hashlib.sha256(open(module.__file__, "rb").read()).hexdigest(),
    }
import system
result["package_version"] = system.__version__
result["search_path"] = list(system.__path__)
print(json.dumps(result))
""" % (repr(MODULES),)


def safe_environment():
    """Never pass deployment credentials or Python path overrides to probes."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(
            ("SUPABASE", "MQTT", "DATABASE", "ADMIN", "CAMERA")
        )
        and key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS"}
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


@pytest.fixture(scope="module")
def installed_python():
    selected = os.environ.get("PHASE3_TEST_PYTHON")
    if not selected:
        pytest.fail(
            "PHASE3_TEST_PYTHON must identify a clean venv with the Wheel installed"
        )
    executable = Path(selected)
    assert executable.is_file()
    config = executable.parent.parent / "pyvenv.cfg"
    assert config.is_file()
    assert "include-system-site-packages = false" in config.read_text().lower()
    return executable


def probe(executable, directory):
    completed = subprocess.run(
        [str(executable), "-B", "-c", PROBE],
        cwd=directory,
        env=safe_environment(),
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    return json.loads(completed.stdout)


def test_previous_wheel_preserved():
    previous = WHEEL.parent / "smart_cylinder_common-0.1.0-py3-none-any.whl"
    assert hashlib.sha256(previous.read_bytes()).hexdigest() == (
        "c059f955fd9f4c44812ca8b26528f30d0d4a9b5dc911259c456885050283af32"
    )


def test_previous_stft_wheel_preserved():
    previous = WHEEL.parent / "smart_cylinder_common-0.1.1-py3-none-any.whl"
    assert hashlib.sha256(previous.read_bytes()).hexdigest() == (
        "6b3ef0d91f9fddf64cb6ef879a16e1ff2aaaa2ddc3c6d9cd218614a95a2f7df5"
    )


def test_previous_realtime_wheel_preserved():
    previous = WHEEL.parent / "smart_cylinder_common-0.1.2-py3-none-any.whl"
    assert (
        hashlib.sha256(previous.read_bytes()).hexdigest()
        == "6defda20d2152e14e13e057ec002a29acf2a69254cb9a0c01f94ba15436a20d2"
    )


def test_previous_web_runtime_wheel_preserved():
    previous = WHEEL.parent / "smart_cylinder_common-0.1.3-py3-none-any.whl"
    assert hashlib.sha256(previous.read_bytes()).hexdigest() == (
        "a2ac5b3ee14a12e15964a11ce03102ba2d5b9195cfcb2a4ae7975f33788abc22"
    )


def test_installed_queue_buffer_stft_runtime(installed_python, tmp_path):
    code = """
import base64, json, threading, uuid
import numpy as np
from system.Backend.Server.sensor_runtime import SensorRuntime, RealtimeConfig, STFTConfig
class Service:
    def __init__(self): self.count = 0
    def record_sensor_message(self, *args, **kwargs): self.count += 1
    def record_runtime(self, identity, metadata, keep):
        assert metadata['stft_status'] == 'COMPLETED'
        assert metadata['operation_status'] == 'REFERENCE_REQUIRED'
done = threading.Event()
service = Service()
config = RealtimeConfig(2, 20000, 12, 30, 128, 512, 10, 10, 2)
rt = SensorRuntime(service, 'TEST', realtime_config=config,
    stft_config={'sph0645': STFTConfig(32, 16, 32, 'hann'),
                 'inmp441': STFTConfig(128, 64, 128, 'hann')},
    stft_callback=lambda result: done.set())
def chunk(rate, count):
    x = np.arange(count, dtype='<i4')
    return dict(sample_rate=rate, sample_format='s32le', sample_count=count,
        encoding='base64', data=base64.b64encode(x.tobytes()).decode())
row = dict(cylinder_id='cylinder_01', session_id=str(uuid.uuid4()), sequence_id=1,
    timestamp='2026-10-06T12:00:00+09:00', sph0645=chunk(4000,64), inmp441=chunk(16000,256))
assert rt.start()
try:
    assert rt.enqueue_message(json.dumps(row), 'smart-cylinder/cylinder_01/sensor')
    assert done.wait(5)
    assert service.count == 1
    assert rt.status == 'REFERENCE_REQUIRED'
    assert set(rt.latest_stft['cylinder_01']['sensors']) == {'sph0645','inmp441'}
finally:
    assert rt.stop()
assert not rt._worker.is_alive()
print('INSTALLED REALTIME RUNTIME PASS')
"""
    result = subprocess.run(
        [str(installed_python), "-B", "-c", code],
        cwd=tmp_path,
        env=safe_environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert "INSTALLED REALTIME RUNTIME PASS" in result.stdout


def test_same_version_different_content_is_not_overwritten(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "common_builder", RENDER_ROOT / "tools" / "build_common_package.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, "OUTPUT_DIR", tmp_path)
    destination = tmp_path / WHEEL.name
    destination.write_bytes(b"existing different artifact")

    def staged_wheel(command, **kwargs):
        output = Path(command[command.index("--wheel-dir") + 1])
        output.mkdir()
        shutil.copy2(WHEEL, output / WHEEL.name)

    monkeypatch.setattr(builder.subprocess, "run", staged_wheel)
    with pytest.raises(RuntimeError, match="different content"):
        builder.build()
    assert destination.read_bytes() == b"existing different artifact"


def test_installed_stft_calculation(installed_python, tmp_path):
    code = """
import numpy as np
from system.ML.Condition.analysis import calculate_stft
x = np.sin(2*np.pi*100*np.arange(4000)/4000)
r = calculate_stft(x, 4000, dict(window_size=400, hop_length=100,
    n_fft=400, window_function='hann'))
assert r.magnitude.shape == (201, 37)
assert np.all(r.frequencies[r.magnitude.argmax(axis=0)] == 100)
print('INSTALLED STFT PASS')
"""
    result = subprocess.run(
        [str(installed_python), "-B", "-c", code],
        cwd=tmp_path,
        env=safe_environment(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "INSTALLED STFT PASS" in result.stdout


def assert_installed_contract(result):
    assert result["version"] == result["package_version"] == "0.1.8"
    site = Path(result["site"]).resolve()
    assert [Path(path).resolve() for path in result["search_path"]] == [site / "system"]
    with zipfile.ZipFile(WHEEL) as archive:
        for name, details in result["modules"].items():
            module_path = Path(details["path"]).resolve()
            assert module_path.is_relative_to(site), (
                "SOURCE TREE SHADOWING: " + name + " selected " + str(module_path)
            )
            relative = module_path.relative_to(site).as_posix()
            assert (
                details["sha256"] == hashlib.sha256(archive.read(relative)).hexdigest()
            )


def assert_source_contract(result):
    """Development selects only the canonical source, matching the release bytes."""
    assert result["version"] == result["package_version"] == "0.1.8"
    assert [Path(path).resolve() for path in result["search_path"]] == [
        PROJECT_ROOT / "system"
    ]
    with zipfile.ZipFile(WHEEL) as archive:
        for name, details in result["modules"].items():
            selected = Path(details["path"]).resolve()
            assert selected.is_relative_to(PROJECT_ROOT / "system"), name
            relative = selected.relative_to(PROJECT_ROOT).as_posix()
            assert (
                details["sha256"] == hashlib.sha256(archive.read(relative)).hexdigest()
            )


def test_wheel_identity_and_content():
    assert hashlib.sha256(WHEEL.read_bytes()).hexdigest() == WHEEL_SHA256
    with zipfile.ZipFile(WHEEL) as archive:
        provenance_name = next(
            name
            for name in archive.namelist()
            if name.endswith("/canonical-source.json")
        )
        manifest = json.loads(archive.read(provenance_name))
        assert manifest["version"] == "0.1.8"
        modules = {name for name in archive.namelist() if name.endswith(".py")}
        assert b"def calculate_stft(" in archive.read("system/ML/Condition/analysis.py")
        assert modules == set(manifest["files"])
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
            source = PROJECT_ROOT / name
            if source.is_file():
                assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        assert all(
            name.endswith(".py") or ".dist-info/" in name for name in archive.namelist()
        )


@pytest.mark.parametrize(
    "location", ["project", "render", "tests", "clean-render", "clean-pi"]
)
def test_installed_import_locations(installed_python, tmp_path, location):
    if location == "project":
        directory = PROJECT_ROOT
    elif location == "render":
        directory = RENDER_ROOT
    elif location == "tests":
        directory = RENDER_ROOT / "tests"
    else:
        directory = tmp_path
    result = probe(installed_python, directory)
    print(location, json.dumps(result, sort_keys=True))
    if location == "project":
        assert_source_contract(result)
    else:
        assert_installed_contract(result)


def test_pytest_environment_uses_canonical_source_or_wheel():
    import system
    import sysconfig

    site = Path(sysconfig.get_path("purelib")).resolve()
    selected = Path(system.__file__).resolve()
    assert selected == PROJECT_ROOT / "system" / "__init__.py" or selected == (
        site / "system" / "__init__.py"
    ), "Pytest selected a duplicate or unknown implementation"


def test_render_server_import_without_database(installed_python, tmp_path):
    shutil.copy2(RENDER_ROOT / "server.py", tmp_path / "server.py")
    shutil.copytree(RENDER_ROOT / "src", tmp_path / "src")
    script = PROBE + r"""
from unittest.mock import patch
import socket
with patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK FORBIDDEN")):
    import server
    assert server.app is not None
    assert server.process_order_storage is None
print("SERVER IMPORT PASS: NO LIVE DB")
"""
    completed = subprocess.run(
        [str(installed_python), "-B", "-c", script],
        cwd=tmp_path,
        env=safe_environment(),
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    assert_installed_contract(json.loads(completed.stdout.splitlines()[0]))
    assert "SERVER IMPORT PASS" in completed.stdout


def test_wrong_implementation_is_rejected():
    with pytest.raises(AssertionError):
        assert_installed_contract(
            {
                "version": "0.1.8",
                "package_version": "0.1.8",
                "site": str(RENDER_ROOT / "uninstalled-site"),
                "search_path": [str(RENDER_ROOT / "system")],
                "modules": {},
            }
        )


def cycle_payload():
    return {
        "cylinder_id": "cylinder_01",
        "session_id": "00000000-0000-4000-8000-000000000001",
        "cycle_id": "00000000-0000-4000-8000-000000000002",
        "sequence_id": 1,
        "timestamp": "2026-10-06T12:00:00+09:00",
        "prediction": "ABNORMAL",
        "ground_truth": "NORMAL",
        "features": {"sph0645_rms": 0.25},
    }


def test_duplicate_package_removed_and_no_implicit_fallback(installed_python):
    """The delivery root contains no second source, even without site-packages."""
    assert not (RENDER_ROOT / "system").exists()
    script = "import system"
    completed = subprocess.run(
        [str(installed_python), "-S", "-c", script],
        cwd=RENDER_ROOT,
        env=safe_environment(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode != 0
    assert "ModuleNotFoundError" in completed.stderr


class QueryRecorder:
    """Record existing Supabase query contracts without network or credentials."""

    def __init__(self, responses=()):
        self.calls = []
        self.responses = iter(responses)

    def table(self, name):
        self.calls.append(("table", name))
        return self

    def upsert(self, row, **options):
        self.calls.append(("upsert", row, options))
        return self

    def select(self, columns):
        self.calls.append(("select", columns))
        return self

    def eq(self, *args):
        self.calls.append(("eq", *args))
        return self

    def in_(self, *args):
        self.calls.append(("in", *args))
        return self

    def limit(self, count):
        self.calls.append(("limit", count))
        return self

    def execute(self):
        from types import SimpleNamespace

        response = next(self.responses, [])
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(data=response)


def test_repository_cycle_session_and_label_separation():
    from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository

    client = QueryRecorder()
    payload = cycle_payload()
    result = CylinderResultRepository(client).save_processed_features_and_ml_result(
        payload
    )
    assert result["session_id"] == payload["session_id"]
    rows = [call for call in client.calls if call[0] == "upsert"]
    assert rows[0][1]["session_id"] == payload["session_id"]
    assert rows[1][1]["prediction"] == "ABNORMAL"
    assert "ground_truth" not in rows[1][1]
    assert [call[1] for call in client.calls if call[0] == "table"] == [
        "processed_features",
        "ml_results",
    ]
    for row in rows:
        assert row[2]["on_conflict"] == "cylinder_id,cycle_id"
        assert "created_at" not in row[1]


@pytest.mark.parametrize("conflict", [False, True])
def test_repository_session_semantics(conflict):
    from system.DB.Supabase.cylinder_result_repository import (
        CylinderResultRepository,
        SessionConflictError,
    )

    session = dict(
        cycle_payload(),
        collection_mode="TEST",
        dataset_type="TEST",
        ground_truth=None,
        ground_truth_source=None,
        started_at="2026-10-06T12:00:00+09:00",
    )
    stored = dict(session)
    if conflict:
        stored["collection_mode"] = "TRAINING"
    client = QueryRecorder([[], [stored]])
    repository = CylinderResultRepository(client)
    if conflict:
        with pytest.raises(SessionConflictError):
            repository.ensure_collection_session(session)
    else:
        assert repository.ensure_collection_session(session) == stored
    assert client.calls[1][2] == {
        "on_conflict": "session_id",
        "ignore_duplicates": True,
    }


def test_repository_raw_identity_and_compaction():
    from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository

    client = QueryRecorder()
    payload = dict(cycle_payload(), sph0645={"samples": [1], "pcm_base64": "AA=="})
    CylinderResultRepository(client).save_raw_data(payload)
    row = client.calls[1]
    assert row[2]["on_conflict"] == "cylinder_id,session_id,sequence_id"
    assert row[1]["raw_payload"]["sph0645"] == {"pcm_base64": "AA=="}
    assert "created_at" not in row[1]


def test_repository_training_labels_come_from_sessions():
    from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository

    client = QueryRecorder(
        [
            [
                {
                    "session_id": "normal",
                    "dataset_type": "TRAINING_NORMAL",
                    "ground_truth": "NORMAL",
                },
                {
                    "session_id": "invalid",
                    "dataset_type": "TRAINING_NORMAL",
                    "ground_truth": "SEAL_LEAK",
                },
            ],
            [
                {"session_id": "normal", "ground_truth": "ABNORMAL"},
                {"session_id": "invalid"},
            ],
        ]
    )
    assert CylinderResultRepository(client).load_training_dataset() == [
        {
            "session_id": "normal",
            "dataset_type": "TRAINING_NORMAL",
            "ground_truth": "NORMAL",
        }
    ]
    assert ("eq", "collection_mode", "TRAINING") in client.calls
    assert ("in", "session_id", ("normal",)) in client.calls


def test_repository_errors_are_safe(monkeypatch):
    from system.DB.Supabase.cylinder_result_repository import (
        CylinderResultRepository,
        RepositoryWriteError,
        RepositoryConfigurationError,
    )

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(RepositoryConfigurationError):
        CylinderResultRepository().save_raw_data(cycle_payload())
    client = QueryRecorder([RuntimeError("private diagnostic")])
    with pytest.raises(
        RepositoryWriteError, match="Supabase raw-data write failed"
    ) as error:
        CylinderResultRepository(client).save_raw_data(cycle_payload())
    assert "private diagnostic" not in str(error.value)


@pytest.mark.parametrize(
    "mode,cylinder,dataset,label",
    [
        ("TRAINING", "cylinder_01", "TRAINING_NORMAL", "NORMAL"),
        ("TRAINING", "cylinder_02", "TRAINING_ABNORMAL", "SEAL_LEAK"),
        ("TEST", "cylinder_01", "TEST", None),
        ("OPERATION", "cylinder_01", "RAW_OPERATION", None),
    ],
)
def test_service_collection_modes(mode, cylinder, dataset, label):
    from system.Backend.Service.cylinder_result_service import resolve_collection_route

    route = resolve_collection_route(mode, cylinder)
    assert route["dataset_type"] == dataset
    assert route["ground_truth"] == label


def test_service_dedup_and_session_before_raw(monkeypatch):
    from unittest.mock import Mock
    from system.Backend.Service.cylinder_result_service import (
        CylinderResultService,
        CollectionModeError,
    )

    repository = Mock()
    service = CylinderResultService(repository)
    payload = cycle_payload()
    assert service.record_sensor_message(payload, is_duplicate=True) is None
    assert not repository.mock_calls
    monkeypatch.delenv("COLLECTION_MODE", raising=False)
    with pytest.raises(CollectionModeError):
        service.record_sensor_message(payload)
    service.record_sensor_message(payload, collection_mode="TEST")
    assert [call[0] for call in repository.mock_calls] == [
        "ensure_collection_session",
        "save_raw_data",
    ]
    with pytest.raises(CollectionModeError):
        service.record_sensor_message(
            dict(payload, cylinder_id="cylinder_03"), collection_mode="TRAINING"
        )


def test_service_delegates_cycle_and_training():
    from unittest.mock import Mock
    from system.Backend.Service.cylinder_result_service import CylinderResultService

    repository = Mock()
    service = CylinderResultService(repository)
    payload = cycle_payload()
    assert (
        service.record_cycle_result(payload)
        == repository.save_processed_features_and_ml_result.return_value
    )
    repository.save_processed_features_and_ml_result.assert_called_once_with(payload)
    assert (
        service.load_training_dataset() == repository.load_training_dataset.return_value
    )


@pytest.mark.parametrize(
    "changed,status",
    [
        ({}, 200),
        ({"session_id": None}, 400),
        ({"session_id": "invalid"}, 400),
        ({"cycle_id": "invalid"}, 400),
        ({"timestamp": "2026-10-06T12:00:00"}, 400),
        ({"prediction": "UNKNOWN"}, 400),
        ({"features": []}, 400),
        ({"leakage_score": True}, 400),
    ],
)
def test_api_validation_and_factory(monkeypatch, changed, status):
    from unittest.mock import Mock
    from system.Backend.API import cylinder_result
    from system.Backend.Server.app import create_app

    service = Mock()
    service.record_cycle_result.return_value = cycle_payload()
    monkeypatch.setattr(cylinder_result, "_service", service)
    response = (
        create_app()
        .test_client()
        .post("/api/cylinder-result", json=dict(cycle_payload(), **changed))
    )
    assert response.status_code == status
    assert service.record_cycle_result.call_count == int(status == 200)


@pytest.mark.parametrize("unavailable,status", [(True, 503), (False, 502)])
def test_api_safe_error_response(monkeypatch, unavailable, status):
    from unittest.mock import Mock
    from system.Backend.API import cylinder_result
    from system.Backend.Service.cylinder_result_service import (
        ResultStorageUnavailableError,
    )
    from system.Backend.Server.app import create_app

    service = Mock()
    service.record_cycle_result.side_effect = (
        ResultStorageUnavailableError("Supabase server credentials are not configured")
        if unavailable
        else RuntimeError("private diagnostic")
    )
    monkeypatch.setattr(cylinder_result, "_service", service)
    response = (
        create_app().test_client().post("/api/cylinder-result", json=cycle_payload())
    )
    assert response.status_code == status
    assert "private diagnostic" not in response.get_data(as_text=True)
