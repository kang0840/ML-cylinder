const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=process.argv[2]||path.join(__dirname,'..'),els=new Map();
function el(id){if(!els.has(id))els.set(id,{value:'',textContent:'',hidden:false,children:[],listeners:{},clientWidth:800,width:800,height:280,addEventListener(k,f){this.listeners[k]=f},appendChild(x){this.children.push(x)},getContext(){return new Proxy({},{get:()=>()=>{}})}});return els.get(id)}
const ids=['pico','ageMode','priorMeasuredCycles','age','sensor','influence','parameterMode','failures','feature'];
const c={Math,Number,Array,Object,Error,Date,URLSearchParams,location:{search:'?pico=cylinder_05'},setTimeout(){},document:{getElementById:el,createElement:()=>({appendChild(){}}),querySelectorAll:()=>ids.map(el)}};
c.window=c;c.devicePixelRatio=1;c.addEventListener=()=>{};vm.createContext(c);
for(const file of ['lifetime-condition-math.js','lifetime-condition-snapshot.js','lifetime-db-usage.js'])vm.runInContext(fs.readFileSync(root+'/public/'+file,'utf8'),c);
const usage=c.LIFETIME_DB_USAGE;
assert(Math.abs(usage.picos.cylinder_05.estimated_interval_cycles-388.7739810811374)<1e-6);
for(const p of Object.values(usage.picos)){assert.equal(p.actual_total_cycles,null);assert.equal(p.prior_lifetime_cycles,null);}
el('sensor').value='inmp441';el('parameterMode').value='fit';el('influence').value='.25';
vm.runInContext(fs.readFileSync(root+'/public/lifetime-condition-ui.js','utf8'),c);
assert(el('dbUsage').textContent.includes('389'));assert(el('dbUsage').textContent.includes('미상'));
assert.equal(el('age').value,'');assert.equal(el('priorMeasuredCycles').value,'');
// DB usage remains visible even if the independent STFT analysis has failed.
const data=c.LIFETIME_CONDITION_SNAPSHOT.picos;
data.cylinder_05.status='ANALYSIS_FAILED';el('pico').listeners.change();
assert(el('analysisContent').hidden);assert(el('dbUsage').textContent.includes('389'));
el('pico').value='cylinder_03';el('pico').listeners.change();
assert(!el('dbUsage').textContent.includes('389'));assert(el('dbUsage').textContent.includes('환산할 확인된 구간 없음'));
assert.equal(el('age').value,'');assert.equal(el('priorMeasuredCycles').value,'');
console.log('PASS automatic interval display, unknown lifetime, independent STFT failure, Pico scope, no invented inputs');
