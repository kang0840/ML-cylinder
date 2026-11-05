(function(){
  'use strict';
  const $=id=>document.getElementById(id),math=window.LifetimeConditionMath;
  let snapshot=null,busy=false,current=null;
  const count=x=>Number(x).toLocaleString('ko-KR',{maximumFractionDigits:0});
  const decimal=x=>Number(x).toLocaleString('ko-KR',{maximumFractionDigits:3});
  const stamp=x=>new Date(x).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});
  const messages={
    DEMO_STOPPED:'시현 분석이 종료됐습니다. Pi의 08. 모의 수명 분석을 실행하면 새 자료부터 비교합니다.',
    WAITING_NEW_DATA:'시현 분석을 시작한 뒤 새로 저장되는 데이터를 기다립니다.',
    LOW_ACOUSTIC_ACTIVITY:'최근 소리 활동성이 낮습니다. 비교 가능한 음향 후보를 기다립니다. 기계 정지 확정은 아닙니다.',
    MORE_ACOUSTIC_CANDIDATES_REQUIRED:'같은 최근 음향 구간의 후보가 더 필요합니다. 연속 운전이나 정해진 왕복 횟수는 요구하지 않습니다.',
    STALE_DEMO_DATA:'새 수신 데이터가 없습니다. 과거 소리를 현재 시현 결과로 사용하지 않습니다.',
    REFERENCE_DATA_REQUIRED:'이 Pico의 정상 기준·동일 실물/조건 또는 활동성 기준 자료를 확인해야 합니다.',
    DEMO_INPUT_REQUIRED:'시현 입력·실물·조건 또는 기준 원본 변경 여부를 확인하세요.',
    DEMO_COMPARISON_READY:'기존 측정 기준과 시현 음향 후보의 STFT 비교가 준비됐습니다.'
  };
  function clearPlot(id){const c=$(id);c.getContext('2d').clearRect(0,0,c.width,c.height);}
  function plot(id,series,axis){
    const canvas=$(id),dpr=window.devicePixelRatio||1,w=Math.max(300,canvas.clientWidth),h=260;
    canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext('2d');c.scale(dpr,dpr);c.clearRect(0,0,w,h);
    const points=series.flatMap(s=>s.points),mx=Math.max(1,...points.map(p=>p[0])),my=Math.max(1,...points.map(p=>p[1]));
    const left=58,right=w-16,top=16,bottom=h-48;c.font='12px Arial';c.fillStyle='#a9bcc6';c.strokeStyle='#29424e';
    for(let i=0;i<=4;i++){const y=bottom-(bottom-top)*i/4;c.beginPath();c.moveTo(left,y);c.lineTo(right,y);c.stroke();c.fillText(decimal(my*i/4),2,y+4);c.fillText(count(mx*i/4),left+(right-left)*i/4-10,bottom+22);}
    for(const s of series){c.strokeStyle=s.color;c.lineWidth=2;c.beginPath();s.points.forEach((p,i)=>{const x=left+p[0]/mx*(right-left),y=bottom-p[1]/my*(bottom-top);i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
    c.fillStyle='#a9bcc6';c.fillText(axis,left,h-5);
  }
  function resetResults(){current=null;for(const id of ['demoMean','demoMedian','demoDistance','demoNearest'])$(id).textContent='비교 대기';clearPlot('demoSpectrum');clearPlot('demoSurvival');}
  function render(){
    resetResults();
    if(!snapshot||!math){$('demoStatus').textContent='시현 비교 자료가 없습니다. Pi에서 08. 모의 수명 분석을 실행하세요.';return;}
    const data=snapshot.picos&&snapshot.picos[$('pico').value];
    const publicSite=location.hostname.endsWith('onrender.com');
    if(!data){$('demoStatus').textContent='이 Pico의 시현 결과가 없습니다.';return;}
    const replay=data.source_mode==='HISTORICAL_REPLAY',old=(Date.now()-new Date(data.generated_at).getTime())>20000;
    $('demoStamp').textContent=(publicSite?'Render 배포 시점 스냅샷 · Pi 실시간 갱신과 별개 · ':'Pi DB 분석 · ')+(replay?'과거 자료 재현 · ':'분석 시작 이후 새 자료 · ')+stamp(data.generated_at)+' KST';
    $('demoStatus').textContent=(messages[data.status]||'시현 분석 상태를 확인하세요.')+(data.reason?' ('+data.reason+')':'');
    if(old&&!replay)$('demoStatus').textContent+=' · 갱신 시각이 오래됐습니다. 현재 시현 결과가 아닙니다.';
    const reference=data.reference||{};
    $('demoCounts').textContent='정상 기준 '+count(reference.normal_packets||0)+'패킷 / 누설 비교 '+count(reference.leak_packets||0)+'패킷 · 시현 조회 '+count(data.sampled_packets||0)+'패킷 / 음향 후보 '+count(data.active_candidates||0)+'개 / 낮은 활동성 제외 '+count(data.low_activity_packets||0)+'개 · Pico DB 보유 '+(data.source_raw_packets==null?'미확인':count(data.source_raw_packets)+'패킷');
    $('demoConditions').textContent=data.identity_conditions_confirmed?'시현 데이터의 실물·조건이 기준과 일치합니다.':'시현 데이터의 실물·조건 일치는 미확인입니다. 같은 Pico 채널의 탐색적 비교입니다.';
    if(data.status!=='DEMO_COMPARISON_READY'||(old&&!replay))return;
    try{
      const referenceMean=Number($('demoPaperMean').value),influence=Number($('demoInfluence').value);
      if(!Number.isInteger(referenceMean)||referenceMean<1)throw Error('논문 수명 기준은 1 이상의 정수 왕복 횟수로 입력하세요.');
      current=math.condition({referenceMean,shape:snapshot.defaults.shape,distance:data.condition_distance,influence});
      $('demoMean').textContent=count(current.mean_life_cycles)+' 왕복';$('demoMedian').textContent=count(current.median_life_cycles)+' 왕복';
      $('demoDistance').textContent='정상 '+decimal(data.normal_distance)+(data.leak_distance==null?' / 누설 기준 부족':' / 누설 '+decimal(data.leak_distance));
      $('demoNearest').textContent=data.reference_closer==='NORMAL'?'정상 기준에 더 가까움':data.reference_closer==='SEAL_LEAK'?'누설 기준에 더 가까움':'두 기준의 구분 불확실';
      $('demoNearest').textContent+=' · 상태 정답 아님';
      const spectrum=data.spectra[$('demoSensor').value],series=[{color:'#43d8b0',points:spectrum.frequency_hz.map((f,i)=>[f,spectrum.normal_mean[i]])}];
      if(spectrum.leak_mean)series.push({color:'#ffb86b',points:spectrum.frequency_hz.map((f,i)=>[f,spectrum.leak_mean[i]])});
      series.push({color:'#64ceff',points:spectrum.frequency_hz.map((f,i)=>[f,spectrum.demo_mean[i]])});plot('demoSpectrum',series,'주파수 Hz · 세로 log(1 + STFT 진폭)');
      plot('demoSurvival',[{color:'#64ceff',points:current.survival.map(p=>[p.cycles,p.probability*100])}],'총 수명 왕복 횟수 · 세로 생존확률 %');
    }catch(error){resetResults();$('demoStatus').textContent=error.message;}
  }
  async function refresh(){
    if(busy)return;busy=true;
    try{
      const response=await fetch('lifetime-demo-snapshot.json?ts='+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(6000)});
      if(!response.ok)throw Error('시현 분석 자료를 아직 제공하지 않습니다. Pi에서 시현 분석을 실행하세요.');
      const next=await response.json();
      if(next.status!=='DEMO_COMPARISON_COLLECTION'||!next.picos)throw Error('시현 분석 자료 형식을 확인하세요.');
      if(!snapshot||new Date(next.generated_at)>=new Date(snapshot.generated_at))snapshot=next;
      render();
    }catch(error){resetResults();$('demoStatus').textContent=error.message;}
    finally{busy=false;}
  }
  for(const id of ['demoPaperMean','demoInfluence','demoSensor','pico'])$(id).addEventListener('change',render);
  $('demoRefresh').addEventListener('click',refresh);
  $('demoReset').addEventListener('click',()=>{$('demoPaperMean').value='167500';$('demoInfluence').value='.25';render();});
  $('demoDownload').addEventListener('click',()=>{
    if(!snapshot)return;const data=snapshot.picos[$('pico').value];
    const blob=new Blob([JSON.stringify({disclosure:'SIMULATION_ONLY; state-conditioned mean life, not measured usage or remaining life',data,scenario:current},null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='SIMULATION_ONLY-demo-comparison.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  window.addEventListener('resize',render);refresh();setInterval(refresh,3000);
})();
