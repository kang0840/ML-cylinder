from src.factory_twin import CONVEYOR_FEATURES, CYLINDER_FEATURES, FactoryDigitalTwin


def test_assets_use_different_sensor_schemas_and_models():
    twin = FactoryDigitalTwin(seed=7)
    conveyor = twin.read_conveyor("CV-01")
    cylinder = twin.read_cylinder("CY-01")

    assert set(CONVEYOR_FEATURES).issubset(conveyor)
    assert set(CYLINDER_FEATURES).issubset(cylinder)
    assert "pressure_mpa" not in cylinder
    assert "motor_current_a" not in conveyor
    assert "temperature_c" not in conveyor
    assert conveyor["sensors"] == ["ADXL345", "INMP441"]
    assert cylinder["sensors"] == ["ADXL345", "INMP441"]
    assert conveyor["ai"]["model"] != cylinder["ai"]["model"]


def test_dummy_sensor_values_stay_in_physical_operating_ranges():
    twin = FactoryDigitalTwin(seed=11)
    conveyor = twin.read_conveyor("CV-02")
    cylinder = twin.read_cylinder("CY-02")

    assert 0.005 <= conveyor["acceleration_rms_g"] <= 0.5
    assert conveyor["acceleration_peak_g"] >= conveyor["acceleration_rms_g"]
    assert 0 <= conveyor["harmonic_energy_ratio"] <= 1
    assert -80 <= cylinder["sound_rms_dbfs"] <= -1
    assert cylinder["acceleration_peak_g"] >= cylinder["acceleration_rms_g"]
    assert 0 <= cylinder["leak_band_energy_ratio"] <= 1


def test_history_can_be_filtered_by_asset_type():
    twin = FactoryDigitalTwin(seed=13)
    twin.read_conveyor("CV-01")
    twin.read_cylinder("CY-01")

    conveyor_history = twin.history_items("conveyor", 10)
    assert len(conveyor_history) == 1
    assert conveyor_history[0]["equipment_type"] == "conveyor"


def test_factory_api_returns_independent_equipment_data():
    from server import app

    client = app.test_client()
    conveyors = client.get("/api/conveyor").get_json()["conveyors"]
    cylinders = client.get("/api/cylinder").get_json()["cylinders"]
    history = client.get("/api/history?type=cylinder&limit=10").get_json()["history"]

    assert {item["conveyor_id"] for item in conveyors} == {"CV-01", "CV-02"}
    assert {item["cylinder_id"] for item in cylinders} == {"CY-01", "CY-02"}
    assert history and all(item["equipment_type"] == "cylinder" for item in history)


def test_legacy_random_metrics_endpoint_is_removed():
    from server import app

    response = app.test_client().get("/api/metrics?serial=SCC-TEST-TEST")
    assert response.status_code == 404


def test_monitoring_page_uses_only_two_sensor_api_fields():
    from pathlib import Path

    public = Path(__file__).parents[1] / "public"
    page = (public / "monitoring.html").read_text(encoding="utf-8")
    script = (public / "monitor.js").read_text(encoding="utf-8")
    combined = page + script

    assert "api/conveyor" in script
    assert "api/cylinder" in script
    assert "api/metrics" not in combined
    for forbidden in ("motor_current_a", "temperature_c", "pressure_mpa", "belt_slip_percent"):
        assert forbidden not in combined
