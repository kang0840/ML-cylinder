(function(){
  'use strict';
  const $=id=>document.getElementById(id), data=window.LIFETIME_CONDITION_SNAPSHOT, math=window.LifetimeConditionMath;
  let current=null;
  const fmt=x=>Number(x).toLocaleString('ko-KR',{maximumFractionDigits:1});
  function field(id){const value=$(id).value.trim();if(!value)throw Error('필수값을 입력하세요.');return Number(value);}
  function plot(id,series,xLabel){
    const canvas=$(id),dpr=window.devicePixelRatio||1,w=Math.max(300,canvas.clientWidth),h=280;
    canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext('2d');c.scale(dpr,dpr);c.clearRect(0,0,w,h);
    const all=series.flatMap(s=>s.points),maxX=Math.max(1,...all.map(p=>p[0])),maxY=Math.max(1,...all.map(p=>p[1])),l=54,r=w-20,t=20,b=h-52;
    c.font='12px Arial';c.fillStyle='#a9bcc6';c.strokeStyle='#29424e';
    for(let i=0;i<=4;i++){const y=b-(b-t)*i/4;c.beginPath();c.moveTo(l,y);c.lineTo(r,y);c.stroke();c.fillText(fmt(maxY*i/4),4,y+4);c.fillText(fmt(maxX*i/4),l+(r-l)*i/4-12,b+21);}
    for(const s of series){c.strokeStyle=s.color;c.lineWidth=2;c.beginPath();s.points.forEach((p,i)=>{const x=l+p[0]/maxX*(r-l),y=b-p[1]/maxY*(b-t);i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
    c.fillStyle='#a9bcc6';c.fillText(xLabel,l,h-8);
  }
  function renderSpectra(){
    const spectrum=data.spectra[$('sensor').value],series=[{color:'#43d8b0',points:spectrum.frequency_hz.map((f,i)=>[f,spectrum.normal_mean_log1p[i]])}];
    if(spectrum.leak_mean_log1p)series.push({color:'#ffb86b',points:spectrum.frequency_hz.map((f,i)=>[f,spectrum.leak_mean_log1p[i]])});
    plot('spectrum',series,'주파수 Hz · 세로 log(1 + STFT 진폭)');
  }
  function update(){
    try{
      const chosen=data.candidates.find(c=>c.key===$('feature').value);if(!chosen)throw Error('분석 자료를 선택하세요.');
      const mode=$('parameterMode').value;
      const failures=$('failures').value.split(/[\s,]+/).filter(Boolean).map(Number);
      const fit=mode==='fit'?math.fit(failures):{scale:field('scale'),shape:field('shape')};
      if(mode==='fit'){$('scale').value=fit.scale.toFixed(3);$('shape').value=fit.shape.toFixed(6);}
      $('scale').disabled=mode==='fit';$('shape').disabled=mode==='fit';$('failures').disabled=mode!=='fit';
      renderSpectra();
      $('distance').textContent=chosen.name+' · 정상 대비 STFT 편차 D='+chosen.distance.toFixed(4);
      if($('ageMode').value!=='manual')throw Error('시간·가정 주기로 사용 횟수를 생성하지 않습니다. 실측 누적 횟수가 필요합니다.');
      const rawAge=$('age').value.trim();
      if(!rawAge)throw Error('누적 사용량 미상: STFT는 표시하며 잔여수명은 계산하지 않습니다.');
      const age=field('age');
      if(!Number.isInteger(age)||age<0)throw Error('실측 누적 왕복 횟수는 0 이상의 정수여야 합니다.');
      current=math.evaluate({age,distance:chosen.distance,influence:field('influence'),...fit});
      current.snapshot_generated_at=data.generated_at;current.selected_feature=chosen.key;
      current.usage_basis='USER_REPORTED_MEASURED_CYCLES';current.measured_age_cycles=age;
      delete current.assumed_age_cycles;
      current.failure_counts=mode==='fit'?failures:null;current.parameter_mode=mode;
      $('mean').textContent=fmt(current.mean_remaining_cycles)+' 회';$('median').textContent=fmt(current.median_remaining_cycles)+' 회';
      $('total').textContent=fmt(current.mean_total_cycles)+' 회';$('used').textContent=fmt(age)+' 회 (실측 입력)';
      $('distance').textContent=chosen.name+' · 정상 대비 편차 D='+chosen.distance.toFixed(4)+(chosen.distance===0?' · 현재 대표값은 정상 변동 범위 안입니다. 정상 판정이나 수명 차이의 검증을 뜻하지 않습니다.':'');
      $('error').textContent='';plot('survival',[{color:'#64ceff',points:current.survival.map(p=>[p.extra_cycles,p.probability*100])}],'추가 사용 횟수 (가정) · 세로 조건부 생존확률 %');
      renderSpectra();
    }catch(e){current=null;$('error').textContent=e.message;for(const id of ['mean','median','total','used'])$(id).textContent='입력 확인';const c=$('survival');c.getContext('2d').clearRect(0,0,c.width,c.height);}
  }
  if(!data||data.status!=='SIMULATION_ONLY'||!math){$('error').textContent='분석 스냅샷이 없습니다. Pi에서 모의 수명 분석을 실행하세요.';return;}
  $('source').textContent=data.cylinder_id+' / '+data.physical_cylinder_id+' · 정상 '+fmt(data.normal_packets)+'패킷 · 누설 '+fmt(data.leak_packets)+'패킷';
  $('stamp').textContent=(data.comparison_scope?'실제 운영 자료의 실물·조건 일치 미확인 · ':'')+'자료 생성: '+new Date(data.generated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST · 실행 시점의 DB 스냅샷';
  $('operation').textContent='관측 운전 '+fmt(data.operation.observed_operation_seconds)+'초 / 제외한 정지 '+(data.operation.excluded_stop_gap_seconds==null?'미확인':fmt(data.operation.excluded_stop_gap_seconds)+'초')+' · 정확한 전원 경계·실왕복 횟수는 미측정';
  for(const candidate of data.candidates){const option=document.createElement('option');option.value=candidate.key;option.textContent=candidate.name;$('feature').appendChild(option);}
  $('feature').value=data.default_assumptions.feature;$('failures').value=data.default_failures.join(', ');
  for(const s of data.sessions){const row=document.createElement('tr');for(const text of [s.experiment_id,s.ground_truth,s.dataset_role,fmt(s.packets),fmt(s.observed_span_seconds)+'초',s.median_distance.toFixed(4)]){const td=document.createElement('td');td.textContent=text;row.appendChild(td);}$('sessions').appendChild(row);}
  for(const input of document.querySelectorAll('input,select,textarea'))input.addEventListener('change',update);
  $('reset').addEventListener('click',()=>{$('age').value='';$('influence').value='.25';$('ageMode').value='manual';$('parameterMode').value='fit';$('failures').value=data.default_failures.join(', ');$('feature').value=data.default_assumptions.feature;update();});
  $('download').addEventListener('click',()=>{if(!current)return;const blob=new Blob([JSON.stringify({disclosure:'SIMULATION_ONLY: 실측 STFT + 논문 기준 + 실측 입력 사용량 + 가설 소리 영향',scenario:current,snapshot:data},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='SIMULATION_ONLY-condition-weibull.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  window.addEventListener('resize',update);const serial=new URLSearchParams(location.search).get('serial');if(serial)$('back').href='monitoring.html?serial='+encodeURIComponent(serial);
  $('age').value='';update();
})();
