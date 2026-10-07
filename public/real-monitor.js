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
  try {
    const response = await fetch(
      `${API_ROOT}/api/real-cylinder?limit=100&source=${dataSource}`
      + (replayMode ? '' : `&serial=${encodeURIComponent(serial)}`)
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `API ${response.status}`);
    if (!replayMode)
    {
        renderCanonical(data);
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
    if (!replayMode)
    {
        renderCanonical({ latest: null });
        $('connection').textContent = error.message === 'MAPPING_REQUIRED'
            ? 'DEVICE MAPPING REQUIRED' : error.message === 'INVALID_SERIAL'
                ? 'INVALID SERIAL' : '데이터 조회 실패 — LIVE 아님';
        $('connection').className = 'connection bad';
        $('note').textContent = error.message;
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
        ? `Web 전용 축약 Preview · SEQ ${latest.stft_preview_sequence_id} · 측정 ${receivedTimeText(latest.stft_preview_timestamp)} · ${latest.live_status === 'STALE' ? '과거 데이터 — STALE' : '현재 Preview의 측정 시각은 위 표시를 확인하세요'}`
        : 'STFT PREVIEW REQUIRED — 설정 또는 계산 데이터 대기';
}

function drawSpectrogram(canvas, preview)
{
    const c = canvas.getContext('2d');
    canvas.width = canvas.clientWidth * (devicePixelRatio || 1);
    canvas.height = canvas.clientHeight * (devicePixelRatio || 1);
    c.clearRect(0, 0, canvas.width, canvas.height);
    if (!preview) return;
    const { relative_times: times, frequencies, magnitude } = preview;
    if (!Array.isArray(times) || !times.length || !Array.isArray(frequencies)
        || !frequencies.length || !Array.isArray(magnitude)
        || magnitude.length !== frequencies.length
        || magnitude.some(row => !Array.isArray(row) || row.length !== times.length)
        || [...times, ...frequencies, ...magnitude.flat()].some(v => !Number.isFinite(v) || v < 0)) return;
    const width = canvas.width - 70;
    const height = canvas.height - 50;
    // Display-only per-preview intensity scale, not a detection threshold.
    let maximum = 0;
    for (const row of magnitude)
    {
        for (const value of row) maximum = Math.max(maximum, value);
    }
    for (let f = 0; f < frequencies.length; f++)
    {
        for (let t = 0; t < times.length; t++)
        {
            const intensity = maximum > 0 ? magnitude[f][t] / maximum : 0;
            c.fillStyle = `rgb(${Math.round(255 * intensity)},${Math.round(180 * intensity)},${Math.round(90 + 165 * intensity)})`;
            c.fillRect(60 + t * width / times.length, 10 + (frequencies.length - 1 - f) * height / frequencies.length,
                width / times.length + 1, height / frequencies.length + 1);
        }
    }
    c.fillStyle = '#eaf2f5';
    c.font = '12px monospace';
    c.fillText(`${frequencies.at(-1).toFixed(1)} Hz`, 0, 18);
    c.fillText(`${frequencies[0].toFixed(1)} Hz`, 0, height + 10);
    c.fillText(`${times[0].toFixed(3)}s`, 60, canvas.height - 22);
    c.fillText(`${times.at(-1).toFixed(3)}s`, canvas.width - 65, canvas.height - 22);
    c.fillText('Relative Time (s) / Frequency (Hz)', 60, canvas.height - 4);
}

addEventListener('resize', refresh);
refresh();
setInterval(refresh, 2000);
