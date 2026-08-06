"""Raspberry Pi 5에서 실행되는 실수 신호 FFT 처리."""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True, slots=True)
class FFTResult:
    frequencies: np.ndarray
    amplitudes: np.ndarray
    spectral_energy: float


def calculate_fft(samples: list[float] | tuple[float, ...] | np.ndarray, sample_rate: int, use_hann_window: bool = True) -> FFTResult:
    """DC 성분을 제거한 뒤 단측 rFFT 진폭과 에너지를 반환한다."""
    signal = np.asarray(samples, dtype=np.float64)
    if signal.ndim != 1 or signal.size < 2 or not np.all(np.isfinite(signal)):
        raise ValueError("samples는 유한한 값 2개 이상인 1차원 배열이어야 합니다.")
    if sample_rate <= 0:
        raise ValueError("sample_rate는 양수여야 합니다.")
    centered = signal - np.mean(signal)
    window = np.hanning(signal.size) if use_hann_window else np.ones(signal.size)
    coherent_gain = float(np.mean(window))
    spectrum = np.fft.rfft(centered * window)
    amplitudes = np.abs(spectrum) * 2.0 / (signal.size * coherent_gain)
    amplitudes[0] /= 2.0
    if signal.size % 2 == 0:
        amplitudes[-1] /= 2.0
    frequencies = np.fft.rfftfreq(signal.size, 1.0 / sample_rate)
    return FFTResult(frequencies, amplitudes, float(np.sum(np.square(amplitudes))))


def band_energy(result: FFTResult, low_hz: float, high_hz: float) -> float:
    """지정 주파수 범위의 제곱 진폭 합을 반환한다."""
    if low_hz < 0 or high_hz <= low_hz:
        raise ValueError("주파수 범위가 올바르지 않습니다.")
    mask = (result.frequencies >= low_hz) & (result.frequencies <= high_hz)
    return float(np.sum(np.square(result.amplitudes[mask])))
