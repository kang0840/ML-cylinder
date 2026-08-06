import numpy as np
from src.fft_processor import calculate_fft

def test_fft_finds_sine_frequency():
    fs = 1600
    samples = np.sin(2*np.pi*100*np.arange(fs)/fs)
    result = calculate_fft(samples, fs)
    assert result.frequencies[np.argmax(result.amplitudes[1:])+1] == 100
