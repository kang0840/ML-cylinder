const query = Object.fromEntries(new URLSearchParams(location.search));
const number = value => Number(value).toLocaleString('ko-KR');

function rngFor(text) {
  let state = Array.from(text).reduce((sum, char) => sum + char.charCodeAt(0), 0) >>> 0;
  return () => {
    state = (state + 0x6D2B79F5) >>> 0;
    let value = state;
    value = Math.imul(value ^ value >>> 15, value | 1);
    value ^= value + Math.imul(value ^ value >>> 7, value | 61);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}

function demoMetrics(serial) {
  const random = rngFor(serial + Math.floor(Date.now() / 5000));
  const good = 12 + Math.floor(random() * 10);
  const defect = Math.floor(random() * 4);
  const noArrival = Math.floor(random() * 3);
  const visionGood = 24 + Math.floor(random() * 16);
  const visionBad = Math.floor(random() * 6);
  const fault = random() > 0.84;
  return {
    discharge: { good, defect, noArrival },
    machining: {
      pressure: +(3.6 + random() * 1.2).toFixed(1),
      cycles: 20 + Math.floor(random() * 18),
      temperature: +(41 + random() * 8).toFixed(1),
      position: 35 + Math.floor(random() * 40), fault
    },
    conveyor: { speed: +(1.1 + random() * 0.7).toFixed(1), count: 120 + Math.floor(random() * 120) },
    vision: {
      good: visionGood, bad: visionBad,
      rate: +(visionGood / (visionGood + visionBad || 1) * 100).toFixed(1),
      lastResult: random() > 0.82 ? 'DEFECT' : 'GOOD'
    },
    event: fault ? '⚠ 가공 실린더 이상 감지 · 점검 필요' : '비전 검사 완료 · 양품 PASS',
    system: { status: fault ? 'fault' : 'run' }
  };
}

async function getMetrics(serial) {
  const apiRoot = location.hostname.endsWith('github.io') ? 'https://ml-cylinder-api-kang0840.onrender.com/' : location.href;
  try {
    const response = await fetch(new URL(`api/metrics?serial=${encodeURIComponent(serial)}`, apiRoot));
    if (!response.ok) throw new Error('데이터를 불러오지 못했습니다.');
    return response.json();
  } catch { return demoMetrics(serial); }
}

function update(data) {
  document.getElementById('cyl1-count').textContent = number(data.discharge.good);
  document.getElementById('cyl2-count').textContent = number(data.discharge.defect);
  document.getElementById('cyl3-count').textContent = number(data.discharge.noArrival);
  document.getElementById('mach-cycles').textContent = `${number(data.machining.cycles)} 회`;
  document.getElementById('mach-temp').textContent = `${data.machining.temperature.toFixed(1)}°C`;
  document.getElementById('mach-pos').textContent = `${data.machining.position}%`;
  document.getElementById('pressure-val').textContent = data.machining.pressure.toFixed(1);
  const arc = document.getElementById('pressure-arc');
  arc.style.strokeDashoffset = 314 - data.machining.pressure / 6.5 * 314;
  arc.style.stroke = data.machining.fault ? 'var(--fault)' : 'var(--run)';

  const banner = document.getElementById('mach-banner');
  const status = document.getElementById('mach-status');
  banner.className = `state-banner ${data.machining.fault ? 'bad' : 'ok'}`;
  document.getElementById('mach-banner-text').textContent = data.machining.fault
    ? '가공 상태 이상 감지 · 온도/압력 확인 필요' : '가공 상태 정상 · 이상 감지 없음';
  status.className = `panel-status ${data.machining.fault ? 'fault' : 'run'}`;
  status.innerHTML = `<span class="led${data.machining.fault ? ' fault' : ''}"></span>${data.machining.fault ? '이상 감지' : '정상'}`;

  document.getElementById('conv-speed').textContent = `${data.conveyor.speed.toFixed(1)} m/min`;
  document.getElementById('conv-count').textContent = number(data.conveyor.count);
  document.getElementById('v-good').textContent = number(data.vision.good);
  document.getElementById('v-bad').textContent = number(data.vision.bad);
  document.getElementById('v-rate').textContent = `${data.vision.rate.toFixed(1)}%`;
  const badge = document.getElementById('last-badge');
  const good = data.vision.lastResult === 'GOOD';
  badge.textContent = `최근 결과 · ${good ? '양품 PASS' : '불량 FAIL'}`;
  badge.style.color = good ? 'var(--run)' : 'var(--fault)';
  badge.style.borderColor = good ? 'rgba(61,220,132,.3)' : 'rgba(255,92,92,.3)';

  const time = new Date().toLocaleTimeString('ko-KR', { hour12: false });
  document.getElementById('log-scroll').innerHTML = `<span>[${time}]</span> ${data.event}`;
  document.getElementById('sys-text').textContent = data.system.status === 'fault' ? 'SYSTEM FAULT' : 'SYSTEM RUN';
  document.getElementById('sys-led').className = `led${data.system.status === 'fault' ? ' fault' : ''}`;
}

function showError(message) {
  document.body.innerHTML = `<main style="padding:32px;color:#e7ebef;background:#0a0d10;min-height:100vh;display:grid;place-items:center;text-align:center;font-family:Arial"><div><h1>오류 발생</h1><p>${message}</p><a href="./index.html" style="color:#4fc3f7">제품 소개 페이지로 돌아가기</a></div></main>`;
}

async function init() {
  const serial = (query.serial || '').toUpperCase();
  if (!serial) return showError('시리얼 넘버가 없습니다. 제품 페이지에서 입력해 주세요.');
  const tag = document.getElementById('serialTag');
  if (tag) tag.textContent = `SERIAL · ${serial}`;
  document.querySelectorAll('a[href="https://smart-cylinder-monitor.com"]').forEach(link => {
    link.href = location.href; link.textContent = location.href.split(/[?#]/)[0];
  });
  update(await getMetrics(serial));
  setInterval(async () => update(await getMetrics(serial)), 5000);
}

addEventListener('DOMContentLoaded', init);
