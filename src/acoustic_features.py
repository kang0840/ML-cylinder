"""두 마이크의 역할별 음향 특징 추출."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from .feature_extractor import extract_features
from .fft_processor import band_energy, calculate_fft

DEFAULT_BANDS = {"low_motion_band": (5.,80.), "fundamental_motion_band": (80.,400.), "harmonic_band": (400.,1200.), "impact_band": (1200.,4000.), "high_frequency_fault_band": (4000.,10000.)}

def load_bands(path: Path | None = None):
    bands = dict(DEFAULT_BANDS)
    path = path or Path(__file__).resolve().parents[1] / "config" / "acoustic_bands.yaml"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if ": [" in line:
                name, values = line.strip().split(":", 1)
                bands[name] = tuple(float(v) for v in json.loads(values.strip()))
    return bands

def validate_bands(bands, sample_rate: int) -> None:
    nyquist = sample_rate / 2
    for name, (low, high) in bands.items():
        if low < 0 or high <= low or high > nyquist:
            raise ValueError(f"{name}={low}~{high} Hz가 나이퀴스트 주파수 {nyquist:g} Hz를 벗어납니다.")

def extract_acoustic_features(samples, sample_rate: int, *, role: str, baseline=None, previous=None):
    bands = load_bands(); validate_bands(bands, sample_rate)
    common = extract_features(samples, sample_rate)
    fft = calculate_fft(samples, sample_rate, detrend=True)
    total = max(fft.spectral_energy, np.finfo(float).eps)
    weights = fft.power / max(float(np.sum(fft.power)), np.finfo(float).eps)
    centroid = float(np.sum(fft.frequencies * weights))
    bandwidth = float(np.sqrt(np.sum((fft.frequencies-centroid)**2 * weights)))
    cumulative = np.cumsum(fft.power)
    rolloff = int(np.searchsorted(cumulative, .85*cumulative[-1])) if cumulative[-1] else 0
    energies = {name: band_energy(fft, *limits) for name, limits in bands.items()}
    fundamental, harmonic = energies["fundamental_motion_band"], energies["harmonic_band"]
    output = {**common, **{f"band_energy_{k}": v for k,v in energies.items()}, "spectral_centroid": centroid, "spectral_bandwidth": bandwidth, "spectral_rolloff_85": float(fft.frequencies[min(rolloff,len(fft.frequencies)-1)]), "harmonic_ratio": harmonic/max(fundamental,np.finfo(float).eps), "fundamental_energy": fundamental, "harmonic_energy": harmonic, "low_frequency_motion": energies["low_motion_band"]/total, "high_frequency_impact": (energies["impact_band"]+energies["high_frequency_fault_band"])/total}
    keys = ["dominant_frequency", "spectral_centroid", *[f"band_energy_{n}" for n in bands]]
    output["baseline_spectral_distance"] = float(np.sqrt(sum((output[k]-float((baseline or {}).get(k,output[k])))**2 for k in keys)))
    output["recent_band_energy_delta"] = float(sum(output[f"band_energy_{n}"]-float((previous or {}).get(f"band_energy_{n}",output[f"band_energy_{n}"])) for n in bands))
    return output
