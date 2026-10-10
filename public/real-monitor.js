const API_ROOT = location.hostname.endsWith('github.io')
  ? 'https://ml-cylinder.onrender.com'
  : window.location.origin;
const replayMode = new URLSearchParams(location.search).get('mode') === 'replay';
const dataSource = replayMode ? 'replay' : 'canonical';
const serial = new URLSearchParams(location.search).get('serial') || '';
const SENSOR_STALE_AFTER_MS = 10_000;
const $ = id => document.getElementById(id);
const labels = {
  forward: '전진', backward: '후진', idle: '정지', normal: '정상',
  pressure_drop: '압력 저하', seal_leak: '씰 누설',
  internal_wear: '내부 마모', unknown: '판정 불가',
};
let lastReceivedAt = null;
let refreshInFlight = false;
let refreshTimer = null;
let lastRenderedData = null;
let failureCount = 0;
const REQUEST_TIMEOUT_MS = 15_000;

function receivedTimeText(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '알 수 없음'
    : date.toLocaleString('ko-KR', {
      timeZone: 'Asia/Seoul', dateStyle: 'short', timeStyle: 'medium',
    });
}

function updateConnectionStatus(measuredAt) {
  const receivedAt = new Date(measuredAt);
    if (!measuredAt || Number.isNaN(receivedAt.getTime()) || receivedAt.getTime() > Date.now())
    {
        $('connection').textContent = 'NO LIVE DATA';
        $('connection').className = 'connection bad';
        return;
    }
  const isStale = !Number.isNaN(receivedAt.getTime())
    && Date.now() - receivedAt.getTime() > SENSOR_STALE_AFTER_MS;
  if (isStale) {
    $('connection').textContent = `센서 데이터 수신 중단 — 마지막 수신: ${receivedTimeText(measuredAt)}`;
    $('connection').className = 'connection bad';
  } else {
    $('connection').textContent = 'LIVE — 데이터 연결됨';
    $('connection').className = 'connection ok';
  }
}

function draw(canvas, lines) {
  const ratio = devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = w * ratio;
  canvas.height = h * ratio;
  const c = canvas.getContext('2d');
  c.scale(ratio, ratio);
  const p = { l: 44, r: 14, t: 14, b: 24 };
  const pw = w - p.l - p.r;
  const ph = h - p.t - p.b;
  const values = lines.flatMap(line => line.values).filter(Number.isFinite);
  let min = values.length ? Math.min(...values) : -1;
  let max = values.length ? Math.max(...values) : 1;
  if (min === max) { min--; max++; }
  c.strokeStyle = '#203039';
  for (let i = 0; i < 5; i++) {
    const y = p.t + ph * i / 4;
    c.beginPath(); c.moveTo(p.l, y); c.lineTo(w - p.r, y); c.stroke();
  }
  lines.forEach(line => {
    c.strokeStyle = line.color;
    c.lineWidth = 2;
    c.beginPath();
    let active = false;
    line.values.forEach((value, index) => {
      if (!Number.isFinite(value))
      {
          active = false;
          return;
      }
      const x = p.l + pw * index / Math.max(line.values.length - 1, 1);
      const y = p.t + (max - value) / (max - min) * ph;
      active ? c.lineTo(x, y) : c.moveTo(x, y);
      active = true;
    });
    c.stroke();

    // Preserve isolated real points on either side of a missing packet.
    if (line.values.length > 1)
    {
        line.values.forEach((value, index) =>
        {
            if (!Number.isFinite(value) || Number.isFinite(line.values[index - 1])
                || Number.isFinite(line.values[index + 1])) return;
            const x = p.l + pw * index / (line.values.length - 1);
            const y = p.t + (max - value) / (max - min) * ph;
            c.fillStyle = line.color;
            c.beginPath();
            c.arc(x, y, 3, 0, Math.PI * 2);
            c.fill();
        });
    }

    // A one-item history has no line segment, so draw its actual point.
    if (line.values.length === 1 && Number.isFinite(line.values[0])) {
      const value = line.values[0];
      const x = p.l + pw / 2;
      const y = p.t + (max - value) / (max - min) * ph;
      c.fillStyle = line.color;
      c.beginPath();
      c.arc(x, y, 4, 0, Math.PI * 2);
      c.fill();
    }
  });
}

async function refresh() {
  if (refreshInFlight) return;
  clearTimeout(refreshTimer);
  refreshInFlight = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let nextDelay = 2000;
  try {
    const response = await fetch(
      `${API_ROOT}/api/real-cylinder?limit=100&source=${dataSource}`
      + (replayMode ? '' : `&serial=${encodeURIComponent(serial)}`),
      { signal: controller.signal }
    );
    if (response.status === 429)
    {
        nextDelay = 60_000;
        throw new Error('요청 제한 — 잠시 후 다시 조회합니다');
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `API ${response.status}`);
    if (!replayMode)
    {
        renderCanonical(data);
        lastRenderedData = data;
        failureCount = 0;
        return;
    }
    const rows = data.history || [];
    const latest = data.latest;
    if (!latest) throw new Error('저장된 측정 데이터가 없습니다');

    lastReceivedAt = latest.measured_at || lastReceivedAt;
    updateConnectionStatus(lastReceivedAt);
    if (replayMode) {
      $('connection').textContent = '재생 데이터 모드 — 실제 센서/PLC 제어와 분리됨';
      $('connection').className = 'connection bad';
    }
    $('motion').textContent = labels[latest.cylinder_state] || latest.cylinder_state;
    $('prediction').textContent = labels[latest.prediction] || latest.prediction;
    $('prediction').className = `value ${latest.prediction === 'normal' ? 'ok' : 'fault'}`;
    $('rul').textContent = latest.remaining_life_percent == null
      ? '수명 데이터 부족'
      : `${Number(latest.remaining_life_percent).toFixed(1)}%`;
    $('sensorRms').textContent = '재생 — 패킷 RMS 없음';
    $('lastReceive').textContent = receivedTimeText(lastReceivedAt);
    draw($('sensorChart'), [
      { values: rows.map(row => row.vibration_rms == null ? NaN : Number(row.vibration_rms)), color: '#42c7ff' },
      { values: rows.map(row => row.sound_rms == null ? NaN : Number(row.sound_rms)), color: '#ffc04c' },
    ]);
    const sourceLabel = replayMode ? '재생 데이터' : '실시간 센서 데이터';
    $('note').textContent = `${sourceLabel} · 최근 ${rows.length}개 결과 · 마지막 측정 ${receivedTimeText(lastReceivedAt)} · RUL 상태: ${latest.rul_status || '수명 데이터 부족'}`;
  } catch (error) {
    failureCount = Math.min(failureCount + 1, 5);
    nextDelay = Math.max(nextDelay, Math.min(30_000, 2000 * 2 ** failureCount));
    if (!replayMode)
    {
        if (!lastRenderedData) renderCanonical({ latest: null });
        $('connection').textContent = error.message === 'MAPPING_REQUIRED'
            ? 'DEVICE MAPPING REQUIRED' : error.message === 'INVALID_SERIAL'
                ? 'INVALID SERIAL' : '데이터 조회 실패 — LIVE 아님';
        $('connection').className = 'connection bad';
        $('note').textContent = (error.name === 'AbortError' ? '조회 시간 초과' : error.message)
            + (lastRenderedData ? ' · 마지막 저장 그래프 유지 — LIVE 아님' : '');
        return;
    }
    if (lastReceivedAt) {
      $('connection').textContent = `센서 데이터 수신 중단 — 마지막 수신: ${receivedTimeText(lastReceivedAt)}`;
      $('connection').className = 'connection bad';
    } else {
      $('connection').textContent = '데이터 연결 오류';
      $('connection').className = 'connection bad';
    }
    $('note').textContent = error.message;
  } finally {
    clearTimeout(timeout);
    refreshInFlight = false;
    refreshTimer = setTimeout(refresh, nextDelay);
  }
}

function renderCanonical(data)
{
    const latest = data.latest;
    lastReceivedAt = latest?.last_received_at || null;
    updateConnectionStatus(lastReceivedAt);
    if (latest?.live_status === 'STALE')
    {
        $('connection').textContent = 'STALE — 저장된 과거 데이터';
        $('connection').className = 'connection bad';
    }
    else if (latest?.live_status !== 'LIVE')
    {
        $('connection').textContent = 'NO LIVE DATA';
        $('connection').className = 'connection bad';
    }
    const text = (id, value, absent) => { if ($(id)) $(id).textContent = value == null ? absent : value; };
    text('motion', latest?.operation_status, 'REFERENCE REQUIRED');
    text('prediction', latest?.prediction, 'NO PREDICTION');
    $('prediction').className = 'value';
    text('health', null, '기준 데이터 부족');
    text('rul', null, '수명 데이터 부족');
    text('vibration', latest?.sph0645_status, 'WAITING FOR SENSOR');
    text('sound', latest?.inmp441_status, 'WAITING FOR SENSOR');
    text('vibrationConfidence', null, 'NO PREDICTION');
    text('soundConfidence', null, 'NO PREDICTION');
    text('stftState', latest?.stft_status, 'STFT CONFIG REQUIRED');
    text('mlState', latest?.ml_status, 'MODEL REQUIRED');
    text('lastReceive', lastReceivedAt ? receivedTimeText(lastReceivedAt) : null, 'NO LIVE DATA');
    text('fftState', latest?.fft_features ? JSON.stringify(latest.fft_features) : null, 'WAITING FOR OPERATION');
    text('sphRms', latest?.packet_metrics?.sph0645?.rms, 'WAITING FOR SENSOR');
    text('inmpRms', latest?.packet_metrics?.inmp441?.rms, 'WAITING FOR SENSOR');
    text('sphPeak', latest?.packet_metrics?.sph0645?.peak, 'WAITING FOR SENSOR');
    text('inmpPeak', latest?.packet_metrics?.inmp441?.peak, 'WAITING FOR SENSOR');
    text('cycleSphRms', latest?.vibration_rms, 'WAITING FOR OPERATION');
    text('cycleInmpRms', latest?.sound_rms, 'WAITING FOR OPERATION');
    const rmsText = sensor => Number.isFinite(latest?.packet_metrics?.[sensor]?.rms)
        ? latest.packet_metrics[sensor].rms.toFixed(2) : '수신 대기';
    text('sensorRms', `${rmsText('sph0645')} / ${rmsText('inmp441')}`, '센서 수신 대기');
    const packetRows = (data.history || []).filter(row => row.session_id === latest?.session_id);
    function packetSeries(sensor)
    {
        const values = [];
        let previous = null;
        for (const row of packetRows)
        {
            if (previous !== null && row.sequence_id !== previous + 1) values.push(null);
            const value = row.packet_metrics?.[sensor]?.rms;
            values.push(Number.isFinite(value) ? value : null);
            previous = row.sequence_id;
        }
        return values;
    }
    draw($('packetChart'), [
        { values: packetSeries('sph0645'), color: '#42c7ff' },
        { values: packetSeries('inmp441'), color: '#ffc04c' },
    ]);
    // A Cycle feature is not a feature for every Raw packet. Show one real
    // Cycle point rather than repeating it along the Raw message history.
    draw($('sensorChart'), [
        { values: Number.isFinite(latest?.vibration_rms) ? [latest.vibration_rms] : [], color: '#42c7ff' },
        { values: Number.isFinite(latest?.sound_rms) ? [latest.sound_rms] : [], color: '#ffc04c' },
    ]);
    $('note').textContent = latest
        ? `${data.cylinder_id} · 측정 ${receivedTimeText(latest.timestamp)} · Pi 수신 ${receivedTimeText(lastReceivedAt)} · Feature ${latest.feature_timestamp ? receivedTimeText(latest.feature_timestamp) : 'WAITING FOR OPERATION'} · Preview SEQ ${latest.stft_preview_sequence_id ?? '--'} · Preview는 분석/학습 입력이 아닙니다`
        : 'NO LIVE DATA — 센서 데이터 또는 장치 설정을 기다립니다';
    for (const sensor of ['sph0645', 'inmp441'])
    {
        drawSpectrogram($(sensor + 'Preview'), latest?.stft_preview?.[sensor]);
    }
    $('previewState').textContent = latest?.stft_preview
        ? `측정 구간 Preview · SEQ ${latest.stft_preview_sequence_id} · 측정 ${receivedTimeText(latest.stft_preview_timestamp)} · ${latest.live_status === 'STALE' ? '과거 측정 · 센서 갱신 중지 — STALE' : 'Preview 측정 시각은 위 표시를 확인하세요'}`
        : 'STFT PREVIEW REQUIRED — 설정 또는 계산 데이터 대기';
}

function drawSpectrogram(canvas, preview)
{
    const c = canvas.getContext('2d');
    const ratio = devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    canvas.width = w * ratio;
    canvas.height = h * ratio;
    c.scale(ratio, ratio);
    c.clearRect(0, 0, w, h);
    c.fillStyle = '#eaf2f5';
    c.font = '13px sans-serif';
    if (!preview)
    {
        c.fillText('STFT 데이터 대기', 16, 30);
        return;
    }
    const { relative_times: times, frequencies, magnitude } = preview;
    if (!Array.isArray(times) || !times.length || !Array.isArray(frequencies)
        || !frequencies.length || !Array.isArray(magnitude)
        || magnitude.length !== frequencies.length
        || magnitude.some(row => !Array.isArray(row) || row.length !== times.length)
        || [...times, ...frequencies, ...magnitude.flat()].some(v => !Number.isFinite(v) || v < 0)
        || times.some((v, i) => i > 0 && v <= times[i - 1])
        || frequencies.some((v, i) => i > 0 && v <= frequencies[i - 1]))
    {
        c.fillText('STFT 표시 데이터 형식 확인 필요', 16, 30);
        return;
    }
    const p = { l: 62, r: 70, t: 30, b: 100 };
    const width = w - p.l - p.r;
    const height = h - p.t - p.b;
    if (width <= 0 || height <= 0) return;
    const topDb = 60; // Display range only; not noise removal or a threshold.
    let maximum = 0;
    for (const row of magnitude)
    {
        for (const value of row) maximum = Math.max(maximum, value);
    }
    // Use actual bin-centre coordinates, not equally spaced array indices.
    // Midpoint edges are display estimates; do not imply extra resolution.
    function edges(values)
    {
        if (values.length === 1)
        {
            const half = values[0] > 0 ? values[0] * 0.05 : 0.5;
            return [Math.max(0, values[0] - half), values[0] + half];
        }
        return [Math.max(0, values[0] - (values[1] - values[0]) / 2),
            ...values.slice(1).map((v, i) => (values[i] + v) / 2),
            values.at(-1) + (values.at(-1) - values.at(-2)) / 2];
    }
    const timeEdges = edges(times);
    const freqEdges = edges(frequencies);
    const x = value => p.l + (value - timeEdges[0]) / (timeEdges.at(-1) - timeEdges[0]) * width;
    const y = value => p.t + height - (value - freqEdges[0]) / (freqEdges.at(-1) - freqEdges[0]) * height;
    // Approximate viridis with interpolated anchors: purple → green → yellow.
    function color(intensity)
    {
        const stops = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
        const index = Math.min(3, Math.floor(intensity * 4));
        const fraction = intensity * 4 - index;
        return `rgb(${stops[index].map((v, i) => Math.round(v + (stops[index + 1][i] - v) * fraction)).join(',')})`;
    }
    for (let f = 0; f < frequencies.length; f++)
    {
        for (let t = 0; t < times.length; t++)
        {
            // 20 log10(amplitude / max), computed in log space to avoid underflow.
            const db = maximum > 0 && magnitude[f][t] > 0
                ? Math.max(-topDb, 20 * (Math.log10(magnitude[f][t]) - Math.log10(maximum))) : -topDb;
            c.fillStyle = color((db + topDb) / topDb);
            c.fillRect(x(timeEdges[t]), y(freqEdges[f + 1]),
                x(timeEdges[t + 1]) - x(timeEdges[t]), y(freqEdges[f]) - y(freqEdges[f + 1]));
        }
    }
    c.fillStyle = '#eaf2f5';
    c.font = '11px sans-serif';
    const tickCount = width < 300 ? 2 : 4;
    for (let i = 0; i <= tickCount; i++)
    {
        const fraction = i / tickCount;
        const tx = p.l + fraction * width;
        const fy = p.t + height - fraction * height;
        const time = timeEdges[0] + fraction * (timeEdges.at(-1) - timeEdges[0]);
        const frequency = freqEdges[0] + fraction * (freqEdges.at(-1) - freqEdges[0]);
        c.strokeStyle = '#ffffff25';
        c.beginPath(); c.moveTo(tx, p.t); c.lineTo(tx, p.t + height); c.stroke();
        c.beginPath(); c.moveTo(p.l, fy); c.lineTo(p.l + width, fy); c.stroke();
        c.textAlign = 'center';
        c.fillText(time.toFixed(2), tx, p.t + height + 18);
        c.textAlign = 'right';
        c.fillText(frequency.toFixed(frequency < 10 ? 1 : 0), p.l - 6, fy + 4);
    }
    // Use exactly the same color mapping for the legend and heatmap.
    const barX = p.l + width + 12;
    for (let i = 0; i < 100; i++)
    {
        c.fillStyle = color(1 - i / 99);
        c.fillRect(barX, p.t + i * height / 100, 12, height / 100 + 1);
    }
    c.fillStyle = '#eaf2f5';
    c.textAlign = 'left';
    c.fillText('강함', barX - 2, p.t - 10);
    c.fillText('약함', barX - 2, p.t + height + 18);
    for (const db of [0, -30, -60]) c.fillText(`${db}`, barX + 17, p.t + (-db / topDb) * height + 4);
    c.font = 'bold 12px sans-serif';
    c.fillText('주파수 (Hz)', 6, 16);
    c.textAlign = 'center';
    c.fillText('측정 구간 내부 시간 (초)', p.l + width / 2, p.t + height + 40);
    c.fillText('상대 진폭 (dB)', w / 2, h - 36);
    c.font = '11px sans-serif';
    c.fillText(maximum > 0 ? '0 dB = 이 센서 Preview의 최대 진폭' : '진폭 모두 0 · 상대 dB 기준 없음', w / 2, h - 18);
    c.textAlign = 'left';
}

addEventListener('resize', () =>
{
    if (lastRenderedData && !replayMode) renderCanonical(lastRenderedData);
});
refresh();
