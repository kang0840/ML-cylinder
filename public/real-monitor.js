const API_ROOT = location.hostname.endsWith('github.io')
  ? 'https://ml-cylinder.onrender.com'
  : window.location.origin;
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
  const isStale = !Number.isNaN(receivedAt.getTime())
    && Date.now() - receivedAt.getTime() > SENSOR_STALE_AFTER_MS;
  if (isStale) {
    $('connection').textContent = `센서 데이터 수신 중단 — 마지막 수신: ${receivedTimeText(measuredAt)}`;
    $('connection').className = 'connection bad';
  } else {
    $('connection').textContent = '데이터 연결됨';
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
    line.values.forEach((value, index) => {
      const x = p.l + pw * index / Math.max(line.values.length - 1, 1);
      const y = p.t + (max - value) / (max - min) * ph;
      index ? c.lineTo(x, y) : c.moveTo(x, y);
    });
    c.stroke();
  });
}

async function refresh() {
  try {
    const response = await fetch(`${API_ROOT}/api/real-cylinder?limit=100`);
    if (!response.ok) throw new Error(`API ${response.status}`);
    const data = await response.json();
    const rows = data.history || [];
    const latest = data.latest;
    if (!latest) throw new Error('저장된 측정 데이터가 없습니다');

    lastReceivedAt = latest.measured_at || lastReceivedAt;
    updateConnectionStatus(lastReceivedAt);
    const vp = latest.vibration_prediction || latest.prediction;
    const sp = latest.sound_prediction || latest.prediction;
    const vc = latest.vibration_confidence ?? latest.confidence;
    const sc = latest.sound_confidence ?? latest.confidence;
    $('motion').textContent = labels[latest.cylinder_state] || latest.cylinder_state;
    $('prediction').textContent = labels[latest.prediction] || latest.prediction;
    $('prediction').className = `value ${latest.prediction === 'normal' ? 'ok' : 'fault'}`;
    $('health').textContent = `${Number(latest.health_score).toFixed(1)}점`;
    $('rul').textContent = latest.remaining_life_percent == null
      ? '수명 데이터 부족'
      : `${Number(latest.remaining_life_percent).toFixed(1)}%`;
    $('vibration').textContent = labels[vp] || vp;
    $('vibrationConfidence').textContent = vc == null ? '--' : `${Number(vc * 100).toFixed(1)}%`;
    $('sound').textContent = labels[sp] || sp;
    $('soundConfidence').textContent = sc == null ? '--' : `${Number(sc * 100).toFixed(1)}%`;
    draw($('sensorChart'), [
      { values: rows.map(row => Number(row.vibration_rms || 0)), color: '#42c7ff' },
      { values: rows.map(row => Number(row.sound_rms || 0)), color: '#ffc04c' },
    ]);
    $('note').textContent = `최근 ${rows.length}개 결과 · 마지막 측정 ${receivedTimeText(lastReceivedAt)} · RUL 상태: ${latest.rul_status || '수명 데이터 부족'}`;
  } catch (error) {
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

addEventListener('resize', refresh);
refresh();
setInterval(refresh, 2000);
