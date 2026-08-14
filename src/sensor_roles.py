"""두 음향 센서의 물리적 역할과 측정 메타데이터."""

SENSOR_METADATA = {
    "sph0645": {"sensor_role": "acoustic_vibration", "measurement_domain": "acoustic", "physical_quantity": "pcm_amplitude", "analysis_method": "fft", "unit": "relative_acoustic_amplitude"},
    "inmp441": {"sensor_role": "sound", "measurement_domain": "acoustic", "physical_quantity": "pcm_amplitude", "analysis_method": "fft", "unit": "relative_acoustic_amplitude"},
}

def metadata_for(sensor_type: str) -> dict[str, str]:
    if sensor_type not in SENSOR_METADATA:
        raise ValueError(f"지원하지 않는 센서입니다: {sensor_type}")
    return dict(SENSOR_METADATA[sensor_type])
