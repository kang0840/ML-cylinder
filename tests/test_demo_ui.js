const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.join(__dirname,'..'),els=new Map();
const bundle=process.argv[2]?JSON.parse(fs.readFileSync(process.argv[2],'utf8')):null;
const source=file=>bundle?bundle[file]:fs.readFileSync(root+'/public/'+file,'utf8');
function el(id){if(!els.has(id))els.set(id,{value:'',textContent:'',clientWidth:800,width:800,height:260,listeners:{},addEventListener(k,f){this.listeners[k]=f},getContext(){return new Proxy({},{get:()=>()=>{}})}});return els.get(id)}
const fields={pico:'cylinder_05',demoPaperMean:'167500',demoInfluence:'.25',demoSensor:'inmp441'};
for(const [id,value] of Object.entries(fields))el(id).value=value;
const spectrum={frequency_hz:[250,500],normal_mean:[4,1],leak_mean:[1,4],demo_mean:[1,3.5]};
const data={status:'DEMO_COMPARISON_READY',cylinder_id:'cylinder_05',generated_at:new Date().toISOString(),source_mode:'NEW_DB_DATA_AFTER_DEMO_START',normal_distance:3,leak_distance:1,condition_distance:1,reference_closer:'SEAL_LEAK',active_candidates:5,sampled_packets:8,low_activity_packets:3,reference:{normal_packets:40,leak_packets:20},spectra:{inmp441:spectrum,sph0645:spectrum}};
let snapshot={status:'DEMO_COMPARISON_COLLECTION',generated_at:data.generated_at,defaults:{shape:2},picos:{cylinder_05:data,cylinder_03:{status:'REFERENCE_DATA_REQUIRED',generated_at:data.generated_at,reference:{}}}},fail=false;
const context={Math,Number,Array,Object,Error,Date,URLSearchParams,AbortSignal,location:{hostname:'ml-cylinder.onrender.com'},setTimeout(){},setInterval(){},fetch:async()=>{
  if(fail)throw Error('test API unavailable');return {ok:true,json:async()=>JSON.parse(JSON.stringify(snapshot))};
},document:{getElementById:el,createElement:()=>({click(){}})}};
context.window=context;context.devicePixelRatio=1;context.addEventListener=()=>{};
vm.createContext(context);
for(const file of ['lifetime-condition-math.js','lifetime-demo-ui.js'])vm.runInContext(source(file),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();
  assert(el('demoMean').textContent.includes(Math.round(167500*Math.exp(-.25)).toLocaleString('ko-KR')));
  assert(el('demoStamp').textContent.includes('Render 배포 시점 스냅샷'));
  assert(el('demoNearest').textContent.includes('누설 기준에 더 가까움'));assert.equal(el('demoSurvival').hidden,false);
  el('demoPaperMean').value='300';el('demoPaperMean').listeners.change();
  assert.equal(el('demoMean').textContent,Math.round(300*Math.exp(-.25)).toLocaleString('ko-KR')+' 왕복');
  el('demoPaperMean').value='300.5';el('demoPaperMean').listeners.change();assert.equal(el('demoMean').textContent,'비교 대기');
  el('demoReset').listeners.click();assert.equal(el('demoPaperMean').value,'167500');
  el('pico').value='cylinder_03';el('pico').listeners.change();assert.equal(el('demoMean').textContent,'비교 대기');
  el('pico').value='cylinder_05';data.status='LOW_ACOUSTIC_ACTIVITY';
  await el('demoRefresh').listeners.click();assert.equal(el('demoMean').textContent,'비교 대기');
  assert(el('demoStatus').textContent.includes('기계 정지 확정은 아닙니다'));assert.equal(el('demoSurvival').hidden,true);
  data.status='DEMO_COMPARISON_READY';data.generated_at=new Date(Date.now()-60000).toISOString();
  await el('demoRefresh').listeners.click();assert.equal(el('demoMean').textContent,'비교 대기');
  assert(el('demoStatus').textContent.includes('현재 시현 결과가 아닙니다'));
  fail=true;await el('demoRefresh').listeners.click();assert.equal(el('demoMean').textContent,'비교 대기');
  console.log('PASS editable paper mean, integer cycles, no required usage, idle/stale/failure clearing, independent Pico and Render snapshot disclosure');
})().catch(e=>{console.error(e);process.exitCode=1;});
