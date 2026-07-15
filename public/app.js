const SERIAL_KEY = 'smart-cylinder-serials';
const isStaticHosting = () => location.hostname.endsWith('github.io') || location.protocol === 'file:';

function formatSerial(value) {
  const cleaned = (value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  return cleaned.startsWith('SCC') && cleaned.length === 11
    ? `${cleaned.slice(0, 3)}-${cleaned.slice(3, 7)}-${cleaned.slice(7)}` : '';
}

function getSerials() {
  try { return JSON.parse(localStorage.getItem(SERIAL_KEY) || '[]'); } catch { return []; }
}

function saveSerial(serial) {
  const serials = getSerials();
  if (!serials.includes(serial)) serials.push(serial);
  localStorage.setItem(SERIAL_KEY, JSON.stringify(serials));
}

function demoPurchase() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const part = () => Array.from({ length: 4 }, () => chars[Math.floor(Math.random() * chars.length)]).join('');
  const serial = `SCC-${part()}-${part()}`;
  saveSerial(serial);
  return { serial };
}

async function request(path, options = {}) {
  if (isStaticHosting()) {
    if (path.includes('/purchase')) return demoPurchase();
    const serial = new URL(path, location.href).searchParams.get('serial') || '';
    return { serial, valid: getSerials().includes(serial) };
  }
  const response = await fetch(path, { ...options, credentials: 'same-origin' });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function showView(name) {
  document.querySelectorAll('.view').forEach(view => view.classList.add('hidden'));
  document.getElementById(`view-${name}`).classList.remove('hidden');
  document.getElementById('moreMenu').classList.add('hidden');
}

function resetForm() {
  document.getElementById('serialMsg').textContent = '';
  document.getElementById('serialInput').classList.remove('err');
}

function setError(message) {
  const input = document.getElementById('serialInput');
  const messageBox = document.getElementById('serialMsg');
  messageBox.textContent = message;
  messageBox.classList.add('err');
  input.classList.add('err');
  input.focus();
}

async function purchase() {
  const button = document.getElementById('buyBtn');
  button.disabled = true;
  button.textContent = '발급 중...';
  try {
    const data = await request('api/purchase', { method: 'POST' });
    document.getElementById('modalSerialText').textContent = data.serial;
    document.getElementById('purchaseModal').classList.remove('hidden');
  } catch (error) {
    console.error(error);
    alert('시리얼 발급에 실패했습니다.');
  } finally {
    button.disabled = false;
    button.textContent = '구매하기 · 시리얼 넘버 발급';
  }
}

async function submitSerial(event) {
  event.preventDefault();
  const serial = formatSerial(document.getElementById('serialInput').value);
  if (!serial) return setError('유효한 시리얼 넘버를 입력하세요. 예: SCC-ABCD-EFGH');
  const button = document.querySelector('.serial-submit');
  button.disabled = true;
  try {
    const data = await request(`api/validate?serial=${encodeURIComponent(serial)}`);
    if (data.valid) location.href = `monitoring.html?serial=${encodeURIComponent(serial)}`;
    else setError('등록되지 않은 시리얼 넘버입니다. 구매 후 발급된 번호를 입력해 주세요.');
  } catch (error) {
    console.error(error);
    setError('서버와 연결할 수 없습니다.');
  } finally { button.disabled = false; }
}

addEventListener('DOMContentLoaded', () => {
  const moreMenu = document.getElementById('moreMenu');
  const moreButton = document.getElementById('moreBtn');
  document.querySelectorAll('a[href="https://smart-cylinder-monitor.com"]').forEach(link => {
    link.href = location.href;
    link.textContent = location.href.split(/[?#]/)[0];
  });
  moreButton.addEventListener('click', event => { event.stopPropagation(); moreMenu.classList.toggle('hidden'); });
  document.addEventListener('click', event => {
    if (!moreMenu.contains(event.target) && event.target !== moreButton) moreMenu.classList.add('hidden');
  });
  document.getElementById('menuGoSerial').addEventListener('click', () => { showView('serial'); resetForm(); });
  document.getElementById('menuGoProduct').addEventListener('click', () => showView('product'));
  document.getElementById('serialBack').addEventListener('click', () => showView('product'));
  document.getElementById('serialInput').addEventListener('input', event => { event.target.value = event.target.value.toUpperCase(); resetForm(); });
  document.getElementById('buyBtn').addEventListener('click', purchase);
  document.getElementById('modalClose').addEventListener('click', () => document.getElementById('purchaseModal').classList.add('hidden'));
  document.getElementById('modalGoSerial').addEventListener('click', () => {
    document.getElementById('purchaseModal').classList.add('hidden');
    showView('serial');
    document.getElementById('serialInput').value = document.getElementById('modalSerialText').textContent;
    resetForm();
  });
  document.getElementById('serialForm').addEventListener('submit', submitSerial);
});
