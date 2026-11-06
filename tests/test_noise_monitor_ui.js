const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const elements=new Map(),draws=[];
function el(id){if(!elements.has(id))elements.set(id,{id,textContent:''});return elements.get(id)}
const context={window:{},document:{getElementById:el},Number,Date};
vm.createContext(context);
const source=process.argv[2]?fs.readFileSync(process.argv[2],'utf8'):fs.readFileSync(path.join(__dirname,'../public/noise-monitor.js'),'utf8');
vm.runInContext(source,context);
function point(q,matched=true){return {sequence_id:q,session_id:'s',noise_reduction:matched?{
  source_received_at:'2026-11-06T04:00:00Z',sensors:{sph0645:{original_ac_rms:100,rms:40},inmp441:{original_ac_rms:200,rms:80}}}:null}}
const draw=(canvas,lines)=>draws.push({id:canvas.id,lines});
context.window.renderNoiseComparison({history:[point(1),point(3),point(4,false)],latest:{session_id:'s'},noise_reduction_status:'READY'},draw);
assert.equal(draws.length,2);
assert.deepEqual(Array.from(draws[0].lines[0].values),[100,null,100,null]);
assert.deepEqual(Array.from(draws[0].lines[1].values),[40,null,40,null]);
assert(el('noiseRmsTime').textContent.includes('SEQ 3'));
assert(el('noiseRmsValues').textContent.includes('100 → 40'));
assert(el('noiseRmsState').textContent.includes('연결됨'));
draws.length=0;
context.window.renderNoiseComparison({history:[point(1,false)],latest:{session_id:'s'},noise_reduction_status:'WAITING_FOR_PI'},draw);
assert(el('noiseRmsValues').textContent.includes('대기'));
assert.deepEqual(Array.from(draws[0].lines[1].values),[null]);
console.log('Noise comparison UI: aligned values, gaps and waiting state PASS');
