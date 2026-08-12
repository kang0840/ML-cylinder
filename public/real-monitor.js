const API_ROOT = window.location.origin;
const $ = id => document.getElementById(id);
const labels = { forward: '전진', backward: '후진', idle: '정지' };

function draw(canvas, lines) {
  const ratio = devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * ratio; canvas.height = h * ratio;
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
  const pad = {l:44,r:14,t:14,b:24}, pw=w-pad.l-pad.r, ph=h-pad.t-pad.b;
  const values = lines.flatMap(line => line.values).filter(Number.isFinite);
  let min = values.length ? Math.min(...values) : -1, max = values.length ? Math.max(...values) : 1;
  if (min === max) { min -= 1; max += 1; }
  const margin=(max-min)*.12; min-=margin; max+=margin;
  ctx.font='10px monospace'; ctx.fillStyle='#78909b'; ctx.strokeStyle='#203039';
  for(let i=0;i<5;i++){const y=pad.t+ph*i/4;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();ctx.fillText((max-(max-min)*i/4).toFixed(1),4,y+3)}
  lines.forEach(line=>{ctx.strokeStyle=line.color;ctx.lineWidth=2;ctx.beginPath();line.values.forEach((v,i)=>{const x=pad.l+pw*i/Math.max(line.values.length-1,1),y=pad.t+(max-v)/(max-min)*ph;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke()});
}

async function refresh(){
  try{
    const response=await fetch(`${API_ROOT}/api/real-cylinder?limit=100`,{headers:{Accept:'application/json'}});
    if(!response.ok) throw new Error(`API ${response.status}`);
    const data=await response.json(), rows=data.history||[], latest=data.latest;
    if(!latest) throw new Error('측정 데이터 없음');
    $('connection').textContent='REAL DATA ONLINE'; $('connection').className='connection ok';
    $('motion').textContent=labels[latest.cylinder_state]||latest.cylinder_state; $('motion').className=`value ${latest.cylinder_state}`;
    $('prediction').textContent=latest.prediction; $('prediction').className=`value ${latest.prediction==='normal'?'':'fault'}`;
    $('health').textContent=`${Number(latest.health_score).toFixed(1)}%`;
    $('measuredAt').textContent=new Date(latest.measured_at).toLocaleString('ko-KR');
    draw($('positionChart'),[{values:rows.map(r=>Number(r.position_index)),color:'#3ce28b'}]);
    draw($('sensorChart'),[{values:rows.map(r=>Number(r.vibration_rms||0)),color:'#42c7ff'},{values:rows.map(r=>Number(r.sound_rms||0)),color:'#ffc04c'}]);
    const states=new Set(rows.map(r=>r.cylinder_state));
    $('note').textContent=states.size===1&&states.has('idle')?'현재 Pico가 모든 패킷을 idle로 보내고 있습니다. forward/backward가 수신되면 위치 그래프가 즉시 상승·하강합니다.':`최근 ${rows.length}건의 실제 센서 DB 데이터를 표시합니다.`;
  }catch(error){$('connection').textContent='SERVER OFFLINE';$('connection').className='connection bad';$('note').textContent=`실데이터 연결 오류: ${error.message}`}
}
addEventListener('resize',refresh); refresh(); setInterval(refresh,2000);
