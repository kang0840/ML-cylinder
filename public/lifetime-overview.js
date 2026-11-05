(function(){
'use strict';
const $=id=>document.getElementById(id),math=window.LifetimeConditionMath;
try{$('overviewMean').textContent=math.pressureScenario().weibull.mean_cycles.toLocaleString('ko-KR',{maximumFractionDigits:0})+' 왕복';}catch(_){$('overviewMean').textContent='계산 자료 확인 필요';}
function render(){
 const key=$('overviewPico').value,source=window.LIFETIME_DB_USAGE,entry=source?.picos?.[key],video=entry?.video_usage;
 const interval=video?.status==='VIDEO_PERIOD_INTERVAL_ESTIMATE'?video.estimated_interval_cycles:null;
 $('overviewCycles').textContent=Number.isFinite(interval)?'약 '+Math.round(interval).toLocaleString('ko-KR')+'회':'확인된 환산 구간 없음';
 $('overviewStamp').textContent=source?.generated_at?'사용횟수 자료: '+new Date(source.generated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' KST · 저장 스냅샷 · 현재 실시간 사용량 아님':'사용횟수 자료 없음';
 const url=new URL($('lifetimeDetails').href,location.href);url.searchParams.set('pico',key);$('lifetimeDetails').href=url.href;
}
$('overviewPico').addEventListener('change',render);render();
})();
