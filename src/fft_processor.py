"""음향 PCM 신호용 FFT 처리."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True, slots=True)
class FFTResult:
    frequencies: np.ndarray
    amplitudes: np.ndarray
    spectral_energy: float
    power: np.ndarray

def calculate_fft(samples, sample_rate: int, use_hann_window: bool = True, detrend: bool = False) -> FFTResult:
    signal = np.asarray(samples, dtype=np.float64)
    if signal.ndim != 1 or signal.size < 2 or not np.all(np.isfinite(signal)):
        raise ValueError("samples는 유한값 2개 이상의 1차원 배열이어야 합니다.")
    if sample_rate <= 0:
        raise ValueError("sample_rate는 양수여야 합니다.")
    centered = signal - np.mean(signal)
    if detrend:
        x = np.arange(signal.size, dtype=float)
        centered -= np.polyval(np.polyfit(x, centered, 1), x)
    window = np.hanning(signal.size) if use_hann_window else np.ones(signal.size)
    gain = float(np.mean(window))
    spectrum = np.fft.rfft(centered * window)
    amplitudes = np.abs(spectrum) * 2.0 / (signal.size * gain)
    amplitudes[0] /= 2.0
    if signal.size % 2 == 0:
        amplitudes[-1] /= 2.0
    frequencies = np.fft.rfftfreq(signal.size, 1.0 / sample_rate)
    power = np.square(amplitudes)
    return FFTResult(frequencies, amplitudes, float(np.sum(power)), power)

def band_energy(result: FFTResult, low_hz: float, high_hz: float) -> float:
    if low_hz < 0 or high_hz <= low_hz:
        raise ValueError("주파수 범위가 올바르지 않습니다.")
    mask = (result.frequencies >= low_hz) & (result.frequencies < high_hz)
    return float(np.sum(result.power[mask]))
