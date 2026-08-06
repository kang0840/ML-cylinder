"""시간 및 주파수 영역 특징 추출."""

from __future__ import annotations

import numpy as np

from .fft_processor import calculate_fft

FEATURE_NAMES = ("mean", "standard_deviation", "rms", "maximum", "minimum", "peak", "peak_to_peak", "crest_factor", "dominant_frequency", "dominant_amplitude", "spectral_energy")


def extract_features(samples: list[float] | tuple[float, ...] | np.ndarray, sample_rate: int, use_hann_window: bool = True) -> dict[str, float]:
    """원시 샘플 하나의 통계와 FFT 특징을 계산한다."""
    signal = np.asarray(samples, dtype=np.float64)
    fft = calculate_fft(signal, sample_rate, use_hann_window)
    abs_signal = np.abs(signal)
    rms = float(np.sqrt(np.mean(np.square(signal))))
    peak = float(np.max(abs_signal))
    start = 1 if fft.amplitudes.size > 1 else 0
    dominant_index = start + int(np.argmax(fft.amplitudes[start:]))
    return {
        "mean": float(np.mean(signal)), "standard_deviation": float(np.std(signal)),
        "rms": rms, "maximum": float(np.max(signal)), "minimum": float(np.min(signal)),
        "peak": peak, "peak_to_peak": float(np.ptp(signal)),
        "crest_factor": peak / rms if rms > np.finfo(float).eps else 0.0,
        "dominant_frequency": float(fft.frequencies[dominant_index]),
        "dominant_amplitude": float(fft.amplitudes[dominant_index]),
        "spectral_energy": fft.spectral_energy,
    }
