const API_ROOT = 'https://ml-cylinder.onrender.com/';
const MAX_POINTS = 40;
const series = {
  conveyor: { rms: [], sound: [] },
  cylinder: { rms: [], sound: [] }
};

const $ = id => document.getElementById(id);
const number = (value, digits = 3) => Number(value).toFixed(digits);
const percent = value => `${(Number(value) * 100).toFixed(1)}%`;

function vibrationInsight(status, current, history) {
  const labels = { NORMAL: '안정', WARNING: '주의', ERROR: '위험', FAULT: '위험' };
  const values = history.filter(Number.isFinite);
  if (!values.length) return { state: labels[status] || status, change: '기준 수집 중', trend: '분석 중' };
  const baseline = values.reduce((sum, value) => sum + value, 0) / values.length;
  const difference = baseline ? ((Number(current) - baseline) / baseline) * 100 : 0;
  const change = Math.abs(difference) < 1 ? '평소와 비슷' : `${Math.abs(difference).toFixed(0)}% ${difference > 0 ? '증가' : '감소'}`;
  if (values.length < 6) return { state: labels[status] || status, change, trend: '분석 중' };
  const recent = values.slice(-6);
  const first = recent.slice(0, 3).reduce((a, b) => a + b, 0) / 3;
  const last = recent.slice(3).reduce((a, b) => a + b, 0) / 3;
  const trendRate = first ? ((last - first) / first) * 100 : 0;
  const trend = Math.abs(trendRate) < 3 ? '변화 없음' : trendRate > 0 ? '증가 중' : '감소 중';
  return { state: labels[status] || status, change, trend };
}

async function api(path) {
  const response = await fetch(new URL(path, API_ROOT), { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`API ${response.status}`);
  return response.json();
}

function setStatus(element, status, label = status) {
  element.className = `status ${status.toLowerCase()}`;
  element.innerHTML = '<i class="dot"></i>';
  element.append(document.createTextNode(label));
}

function updateConveyor(data) {
  const insight = vibrationInsight(data.status, data.acceleration_rms_g, series.conveyor.rms);
  $('conveyorTitle').textContent = `컨베이어 · ${data.conveyor_id}`;
  setStatus($('conveyorStatus'), data.status);
  $('cvVibrationState').textContent = insight.state;
  $('cvChange').textContent = insight.change;
  $('cvTrend').textContent = insight.trend;
  $('cvRms').textContent = `${number(data.acceleration_rms_g)} g`;
  $('cvFrequency').textContent = `${number(data.dominant_vibration_frequency_hz, 2)} Hz`;
  $('cvConfidence').textContent = percent(data.ai.confidence);
  $('cvPeak').textContent = `${number(data.acceleration_peak_g)} g`;
  $('cvCrest').textContent = number(data.crest_factor);
  $('cvHarmonic').textContent = percent(data.harmonic_energy_ratio);
  $('cvSound').textContent = `${number(data.sound_rms_dbfs, 1)} dBFS`;
  $('cvHighBand').textContent = percent(data.acoustic_high_band_ratio);
  $('cvModel').textContent = data.ai.model;
}

function updateCylinder(data) {
  const insight = vibrationInsight(data.status, data.acceleration_rms_g, series.cylinder.rms);
  $('cylinderTitle').textContent = `가공 실린더 · ${data.cylinder_id}`;
  setStatus($('cylinderStatus'), data.status, `${data.status} · Zone ${data.zone}`);
  $('cyVibrationState').textContent = insight.state;
  $('cyChange').textContent = insight.change;
  $('cyTrend').textContent = insight.trend;
  $('cyZone').textContent = data.zone;
  $('cyHealth').textContent = `${data.health_score}/100`;
  $('cyConfidence').textContent = percent(data.ai.confidence);
  $('cyRms').textContent = `${number(data.acceleration_rms_g)} g`;
  $('cyPeak').textContent = `${number(data.acceleration_peak_g)} g`;
  $('cyCrest').textContent = number(data.crest_factor);
  $('cyImpact').textContent = `${number(data.impact_energy_g2_s, 4)} g²·s`;
  $('cyStroke').textContent = `${number(data.stroke_duration_ms, 1)} ms`;
  $('cyLeak').textContent = percent(data.leak_band_energy_ratio);
  $('cySound').textContent = `${number(data.sound_rms_dbfs, 1)} dBFS`;
  $('cyCycles').textContent = Number(data.detected_cycle_count_session).toLocaleString('ko-KR');
  $('cyRemaining').textContent = `약 ${Number(data.estimated_remaining_cycles).toLocaleString('ko-KR')} 회`;
  $('cyModel').textContent = data.ai.model;
}

function appendSeries(type, data) {
  series[type].rms.push(Number(data.acceleration_rms_g));
  series[type].sound.push(Number(data.sound_rms_dbfs));
  for (const key of ['rms', 'sound']) {
    if (series[type][key].length > MAX_POINTS) series[type][key].shift();
  }
}

function drawChart(canvas, first, second, options) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const pad = { left: 44, right: 14, top: 14, bottom: 25 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const values = [...first, ...second];
  let min = values.length ? Math.min(...values) : options.defaultMin;
  let max = values.length ? Math.max(...values) : options.defaultMax;
  const margin = Math.max((max - min) * 0.2, options.minMargin);
  min -= margin; max += margin;
  ctx.strokeStyle = '#22313b'; ctx.fillStyle = '#718592'; ctx.font = '10px monospace'; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + plotHeight * i / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
    const label = (max - (max - min) * i / 4).toFixed(options.decimals);
    ctx.fillText(label, 5, y + 3);
  }
  const drawLine = (data, color) => {
    if (!data.length) return;
    ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2;
    data.forEach((value, index) => {
      const x = pad.left + plotWidth * index / Math.max(MAX_POINTS - 1, 1);
      const y = pad.top + (max - value) / (max - min || 1) * plotHeight;
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  drawLine(first, '#42c7ff');
  drawLine(second, '#39df88');
  ctx.fillStyle = '#42c7ff'; ctx.fillRect(width - 145, 8, 10, 3); ctx.fillStyle = '#8ba0ad'; ctx.fillText('컨베이어', width - 130, 13);
  ctx.fillStyle = '#39df88'; ctx.fillRect(width - 73, 8, 10, 3); ctx.fillStyle = '#8ba0ad'; ctx.fillText('실린더', width - 58, 13);
}

function drawCharts() {
  drawChart($('rmsChart'), series.conveyor.rms, series.cylinder.rms, { defaultMin: 0, defaultMax: 0.2, minMargin: 0.01, decimals: 3 });
  drawChart($('soundChart'), series.conveyor.sound, series.cylinder.sound, { defaultMin: -50, defaultMax: -10, minMargin: 2, decimals: 1 });
}

function setConnection(ok, message) {
  const element = $('connection');
  element.className = `connection ${ok ? 'connected' : 'error'}`;
  element.innerHTML = '<i class="dot"></i>';
  element.append(document.createTextNode(message));
}

async function loadHistory() {
  const data = await api('api/history?limit=100');
  const conveyorId = $('conveyorSelect').value;
  const cylinderId = $('cylinderSelect').value;
  for (const item of data.history) {
    if (item.equipment_type === 'conveyor' && item.conveyor_id === conveyorId) appendSeries('conveyor', item);
    if (item.equipment_type === 'cylinder' && item.cylinder_id === cylinderId) appendSeries('cylinder', item);
  }
}

async function refresh() {
  const conveyorId = $('conveyorSelect').value;
  const cylinderId = $('cylinderSelect').value;
  try {
    const [conveyor, cylinder] = await Promise.all([
      api(`api/conveyor?conveyor_id=${encodeURIComponent(conveyorId)}`),
      api(`api/cylinder?cylinder_id=${encodeURIComponent(cylinderId)}`)
    ]);
    updateConveyor(conveyor);
    updateCylinder(cylinder);
    appendSeries('conveyor', conveyor);
    appendSeries('cylinder', cylinder);
    drawCharts();
    const now = new Date();
    $('timestamp').textContent = `마지막 갱신 ${now.toLocaleString('ko-KR')}`;
    $('eventTime').textContent = `[${now.toLocaleTimeString('ko-KR', { hour12: false })}]`;
    const warnings = [conveyor.status !== 'NORMAL' ? `${conveyorId} ${conveyor.status}` : '', cylinder.status !== 'NORMAL' ? `${cylinderId} ${cylinder.status} · Zone ${cylinder.zone}` : ''].filter(Boolean);
    $('eventText').textContent = warnings.length ? `점검 필요: ${warnings.join(' / ')}` : '두 설비의 센서 신호와 AI 상태가 정상 범위입니다.';
    setConnection(true, 'SERVER ONLINE');
  } catch (error) {
    console.error(error);
    setConnection(false, 'SERVER OFFLINE');
    $('eventText').textContent = '서버에서 센서 데이터를 가져오지 못했습니다. 임의 대체값은 표시하지 않습니다.';
  }
}

function resetHistory() {
  for (const type of Object.values(series)) { type.rms.length = 0; type.sound.length = 0; }
  refresh();
}

addEventListener('DOMContentLoaded', async () => {
  $('conveyorSelect').addEventListener('change', resetHistory);
  $('cylinderSelect').addEventListener('change', resetHistory);
  addEventListener('resize', drawCharts);
  try { await loadHistory(); } catch (error) { console.warn('History unavailable', error); }
  await refresh();
  setInterval(refresh, 2000);
});
