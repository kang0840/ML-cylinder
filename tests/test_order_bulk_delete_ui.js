// Exercise the deployed UI with a simulated API; no production requests.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const path = process.argv[2] || require('path').join(__dirname, '../order-system.html');
const source = fs.readFileSync(path, 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {value:'', innerHTML:'', textContent:'', style:{}, disabled:false,
    addEventListener(){}, appendChild(){}, querySelectorAll(){return [];}});
  return elements.get(id);
}
const ids = ['00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002'];
let records = ids.map(order_id => ({order_id, product_name:'test', requested_product:'A', status:'REQUESTED'}));
let calls = [], confirm = true, fail = false, release;
const context = {console, setInterval(){}, AbortSignal, window:{confirm:()=>confirm},
  document:{getElementById:element, createElement:()=>element('created')},
  fetch:async (url, options={}) => {
    if (url.includes('bulk-delete')) {
      calls.push(JSON.parse(options.body));
      if (fail) return {ok:false, json:async()=>({message:'database failure'})};
      const selected = JSON.parse(options.body).order_ids;
      records = records.filter(o=>!selected.includes(o.order_id));
      return {ok:true, json:async()=>({deleted:true, deleted_count:selected.length, order_ids:selected})};
    }
    if (release) return new Promise(resolve => release(resolve));
    return {ok:true, json:async()=>({orders:records.map(o=>({...o}))})};
  }};
vm.createContext(context);
vm.runInContext(source, context);
const run = code => vm.runInContext(code, context);
(async()=>{
  await run('loadOrders(false)');
  run(`selectedOrderIds.add('${ids[0]}'); renderOrders()`);
  confirm=false; await run('deleteSelectedOrders()'); assert.equal(calls.length,0);
  confirm=true; fail=true; await run('deleteSelectedOrders()');
  assert.equal(run('orders.length'),2); assert.equal(run('selectedOrderIds.size'),1);
  assert(element('notice').textContent.includes('실패'));
  fail=false;
  // Hold an old list response and deliver it after deletion: deleted records must not reappear.
  let resolveOld; release=resolve=>{resolveOld=resolve;};
  const old=run('loadOrders(false)');
  release=null;
  await run('deleteSelectedOrders()');
  resolveOld({ok:true,json:async()=>({orders:ids.map(order_id=>({order_id}))})}); await old;
  assert.equal(run('orders.length'),1); assert.equal(run('orders[0].order_id'),ids[1]);
  assert.equal(run('selectedOrderIds.size'),0);
  assert.deepEqual(calls.at(-1).order_ids,[ids[0]]);
  assert.deepEqual(calls.at(-1).confirm_order_ids,[ids[0]]);
  console.log('PASS confirmation, failed DB deletion preservation, exact IDs, stale poll response protection');
})().catch(e=>{console.error(e);process.exitCode=1;});
