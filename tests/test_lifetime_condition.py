"""Numeric references and Python/browser parity; no DB or PLC writes."""
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('condition_sim',ROOT/'tools/lifetime_condition_simulation.py')
model=importlib.util.module_from_spec(spec);spec.loader.exec_module(model)


class ConditionScenarioTests(unittest.TestCase):
    def test_video_period_applies_only_to_confirmed_interval_not_lifetime(self):
        from copy import deepcopy
        data=json.loads((ROOT/'public/lifetime-condition-snapshot.js').read_text(encoding='utf-8').split('=',1)[1].strip().rstrip(';'))
        data=data.get('picos',{}).get('cylinder_05',data)
        result=model.apply_video_period(deepcopy(data))['video_usage']
        self.assertEqual(result['status'],'VIDEO_PERIOD_INTERVAL_ESTIMATE')
        self.assertAlmostEqual(result['estimated_interval_cycles'],1011.437089/2.6016069444444443,places=6)
        self.assertIsNone(result['prior_lifetime_cycles'])
        self.assertIsNone(result['estimated_total_cycles'])
        self.assertIsNone(result['actual_measured_cycles'])
        wrong=deepcopy(data);wrong['cylinder_id']='cylinder_01'
        self.assertIsNone(model.apply_video_period(wrong)['video_usage']['estimated_interval_cycles'])
        wrong=deepcopy(data);wrong['operation']['confirmed_stops']=False
        self.assertIsNone(model.apply_video_period(wrong)['video_usage']['estimated_interval_cycles'])
        wrong=deepcopy(data);wrong['sessions']=[s for s in wrong['sessions'] if s['session_id']!=next(iter(model.CONFIRMED_STOP_SESSIONS))]
        self.assertIsNone(model.apply_video_period(wrong)['video_usage']['estimated_interval_cycles'])

    def test_paper_mle_and_scale_equivariance(self):
        fit=model.fit_weibull(model.PAPER_FAILURES)
        self.assertAlmostEqual(fit['shape'],7.51525,places=4)
        self.assertAlmostEqual(fit['mean'],135359.63555,delta=1)
        scaled=model.fit_weibull([x*2 for x in model.PAPER_FAILURES])
        self.assertAlmostEqual(scaled['shape'],fit['shape'],places=4)
        self.assertAlmostEqual(scaled['scale'],fit['scale']*2,delta=.1)
        with self.assertRaises(ValueError):model.fit_weibull([100,100])

    def test_conditional_mean_and_median_against_closed_forms(self):
        for age in [0,10,10000]:
            result=model.scenario(age,0,0,100,1)
            self.assertAlmostEqual(result['mean_remaining_cycles'],100,places=7)
            self.assertAlmostEqual(result['median_remaining_cycles'],100*math.log(2),places=7)
        for shape in [.2,.5,1,2,7.5,50]:
            self.assertAlmostEqual(model.conditional_mean(0,100,shape),100*math.gamma(1+1/shape),places=6)
        a,scale=20,100
        expected=scale*math.sqrt(math.pi)/2*math.exp((a/scale)**2)*math.erfc(a/scale)
        self.assertAlmostEqual(model.conditional_mean(a,scale,2),expected,places=7)
        r=model.scenario(a,0,0,scale,2)
        self.assertAlmostEqual(r['median_remaining_cycles'],math.sqrt(a*a+scale*scale*math.log(2))-a,places=7)

    def test_assumptions_bounds_and_survival(self):
        healthy=model.scenario(50000,0,.25,144177,7.5)
        changed=model.scenario(50000,2,.25,144177,7.5)
        self.assertLess(changed['mean_remaining_cycles'],healthy['mean_remaining_cycles'])
        self.assertEqual(changed['status'],'SIMULATION_ONLY')
        curve=changed['survival']
        self.assertEqual(curve[0]['probability'],1)
        self.assertTrue(all(a['probability']>=b['probability'] for a,b in zip(curve,curve[1:])))
        self.assertAlmostEqual(curve[-1]['probability'],1e-10,places=12)
        for invalid in [float('nan'),-1,float('inf')]:
            with self.assertRaises(ValueError):model.scenario(invalid,0,.25,100,2)

    def test_browser_math_matches_python_including_extreme_inputs(self):
        cases=[dict(age=a,distance=d,influence=.25,scale=144177,shape=k)
               for a in [0,1,50000,500000] for d in [0,2] for k in [.2,.5,1,2,7.5,30]]
        cases.append(dict(age=1,distance=0,influence=0,scale=1e8,shape=50))
        code="const m=require(process.argv[1]),cases=JSON.parse(require('fs').readFileSync(0,'utf8'));console.log(JSON.stringify({fit:m.fit([95000,125997,141000,150000,162000]),results:cases.map(m.evaluate)}))"
        completed=subprocess.run(['node','-e',code,str(ROOT/'public/lifetime-condition-math.js')],input=json.dumps(cases),capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
        actual=json.loads(completed.stdout)
        self.assertAlmostEqual(actual['fit']['shape'],model.fit_weibull(model.PAPER_FAILURES)['shape'],places=4)
        for case,browser in zip(cases,actual['results']):
            expected=model.scenario(**case)
            for key in ['mean_remaining_cycles','median_remaining_cycles','mean_total_cycles']:
                self.assertAlmostEqual(browser[key],expected[key],delta=max(abs(expected[key])*1e-7,1e-7))

    def test_web_controls_use_actual_snapshot_and_clear_invalid_results(self):
        code=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert'),root=process.argv[1];
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{value:'',textContent:'',disabled:false,clientWidth:800,width:800,height:280,children:[],listeners:{},addEventListener(k,f){this.listeners[k]=f},appendChild(x){this.children.push(x)},getContext(){return new Proxy({},{get:()=>()=>{}})}});return elements.get(id)}
for(const [id,value] of Object.entries({parameterMode:'fit',ageMode:'manual',age:'50000',influence:'.25',period:'',prior:'0',sensor:'inmp441'}))element(id).value=value;
const ids=['parameterMode','ageMode','age','priorMeasuredCycles','influence','period','prior','sensor','shape','scale','failures','feature'];
const c={console,Math,Number,Array,Object,Error,Date,URLSearchParams,location:{search:''},setTimeout(){},document:{getElementById:element,createElement:()=>({appendChild(){}}),querySelectorAll:()=>ids.map(element)}};
c.window=c;c.devicePixelRatio=1;c.addEventListener=()=>{};
vm.createContext(c);for(const f of ['lifetime-condition-math.js','lifetime-condition-snapshot.js','lifetime-condition-ui.js'])vm.runInContext(fs.readFileSync(root+'/public/'+f,'utf8'),c);
assert(element('error').textContent.includes('누적 사용량 미상'));assert.equal(element('mean').textContent,'입력 확인');
assert.equal(element('ageMode').value,'auto');assert.equal(element('age').value,'');assert(element('used').textContent.includes('389'));assert(element('dbUsage').textContent.includes('389'));
assert(element('videoUsage').textContent.includes('2.6016'));assert(element('videoUsage').textContent.includes('389'));
element('ageMode').value='manual';element('age').value='100';element('age').listeners.change();assert.equal(element('error').textContent,'');assert(element('used').textContent.includes('실측 입력'));
element('age').value='100.5';element('age').listeners.change();assert(element('error').textContent.includes('정수'));assert.equal(element('mean').textContent,'입력 확인');
element('ageMode').value='operation';element('ageMode').listeners.change();assert(element('error').textContent.includes('생성하지 않습니다'));assert.equal(element('mean').textContent,'입력 확인');
element('reset').listeners.click();assert(element('error').textContent.includes('누적 사용량 미상'));assert.equal(element('age').value,'');
element('ageMode').value='video';element('priorMeasuredCycles').value='';element('priorMeasuredCycles').listeners.change();assert(element('error').textContent.includes('이전'));
element('priorMeasuredCycles').value='100';element('priorMeasuredCycles').listeners.change();assert.equal(element('error').textContent,'');assert(element('used').textContent.includes('환산 포함'));
element('ageMode').value='manual';element('age').value='100';
element('failures').value='100,100';element('failures').listeners.change();assert(element('error').textContent.length>0);assert.equal(element('mean').textContent,'입력 확인');
console.log('PASS snapshot counts, scenario updates, observed time conversion, invalid input clearing and reset');
'''
        result=subprocess.run(['node','-e',code,str(ROOT)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_all_entry_points_require_measured_usage(self):
        html=(ROOT/'public/lifetime-condition.html').read_text(encoding='utf-8')
        self.assertNotIn('id="period"',html)
        self.assertNotIn('id="prior"',html)
        self.assertNotIn('value="50000"',html)
        self.assertIn('실측 누적 사용 횟수',html)
        legacy=(ROOT/'public/lifetime-simulation.html').read_text(encoding='utf-8')
        self.assertIn('location.replace("lifetime-condition.html" + location.search',legacy)
        self.assertNotIn('lifetime-simulation-data.js',legacy)
        monitor=(ROOT/'public/monitoring.html').read_text(encoding='utf-8')
        self.assertIn("'lifetime-condition.html?serial='",monitor)
        self.assertNotIn("'lifetime-simulation.html?serial='",monitor)
        snapshot=(ROOT/'public/lifetime-condition-snapshot.js').read_text(encoding='utf-8')
        data=json.loads(snapshot.split('=',1)[1].strip().rstrip(';'))
        data=data.get('picos',{}).get('cylinder_05',data)
        self.assertIsNone(data['default_assumptions']['age_cycles'])
        self.assertIsNone(data['default_result'])


class PerPicoDatabaseTests(unittest.TestCase):
    def test_separate_read_only_archives_and_independent_missing_data(self):
        import base64,sqlite3,struct,tempfile
        from uuid import UUID
        from datetime import datetime,timedelta,timezone
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs') as folder:
            self.assertTrue(Path(folder).resolve().is_relative_to((ROOT/'outputs').resolve()))
            for number in range(1,7):
                cylinder='cylinder_%02d'%number
                db=sqlite3.connect(Path(folder)/('pico%02d-analysis.sqlite3'%number))
                db.executescript('CREATE TABLE identity(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE sessions(session_id TEXT PRIMARY KEY,document TEXT);CREATE TABLE raw_data(id INTEGER PRIMARY KEY,session_id TEXT,sequence_id INTEGER,document TEXT);')
                db.execute('INSERT INTO identity VALUES (?,?)',('cylinder_id',cylinder))
                if number in (1,5):
                    session=str(UUID(int=number));stamp=datetime(2026,11,4,tzinfo=timezone(timedelta(hours=9)))
                    meta=dict(session_id=session,cylinder_id=cylinder,physical_cylinder_id='CYL-%03d'%number,
                              ground_truth='NORMAL',collection_mode='TRAINING',dataset_role='BASELINE',
                              experiment_id='normal_%02d'%number,test_conditions={},started_at=stamp.isoformat())
                    db.execute('INSERT INTO sessions VALUES (?,?)',(session,json.dumps(meta)))
                    for sequence in range(1,4):
                        payload=dict(cylinder_id=cylinder,session_id=session,sequence_id=sequence,
                                     timestamp=(stamp+timedelta(seconds=sequence)).isoformat(),
                                     runtime={'last_received_at':(stamp+timedelta(seconds=sequence)).isoformat()})
                        for sensor,rate,count in [('sph0645',4000,256),('inmp441',16000,1024)]:
                            frequency=250 if number==1 else 500
                            samples=[round((100+sequence*30)*math.sin(2*math.pi*frequency*i/rate)) for i in range(count)]
                            payload[sensor]=dict(sample_rate=rate,sample_count=count,sample_format='s32le',encoding='base64',data=base64.b64encode(struct.pack('<%di'%count,*samples)).decode())
                        row=dict(id=sequence,cylinder_id=cylinder,session_id=session,sequence_id=sequence,raw_payload=payload)
                        db.execute('INSERT INTO raw_data VALUES (?,?,?,?)',(sequence,session,sequence,json.dumps(row)))
                db.commit();db.close()
            result=model.analyze_picos(Path(folder),progress=lambda *a,**k:None)
            self.assertEqual(result['calculated_picos'],['cylinder_01','cylinder_05'])
            for cylinder,expected in [('cylinder_01',250),('cylinder_05',500)]:
                data=result['picos'][cylinder]
                self.assertEqual(data['source_file'],'pico'+cylinder[-2:]+'-analysis.sqlite3')
                self.assertEqual(data['normal_packets'],3)
                self.assertEqual(data['default_assumptions']['feature'],'NORMAL')
                spectrum=data['spectra']['inmp441'];peak=max(range(len(spectrum['normal_mean_log1p'])),key=lambda i:spectrum['normal_mean_log1p'][i])
                self.assertEqual(spectrum['frequency_hz'][peak],expected)
            self.assertEqual(result['picos']['cylinder_02']['reason'],'NORMAL_BASELINE_REQUIRED')
            # No fallback to labeled experiments when operation is absent.
            operation=model.analyze_picos(Path(folder),operation_since='all',progress=lambda *a,**k:None)
            self.assertEqual(operation['picos']['cylinder_01']['reason'],'OPERATION_DATA_REQUIRED')
            db=sqlite3.connect(Path(folder)/'pico01-analysis.sqlite3')
            meta=json.loads(db.execute('SELECT document FROM sessions').fetchone()[0])
            meta.update(session_id=str(UUID(int=101)),collection_mode='OPERATION',ground_truth=None,physical_cylinder_id=None,test_conditions={})
            db.execute('INSERT INTO sessions VALUES (?,?)',(meta['session_id'],json.dumps(meta)))
            row=json.loads(db.execute('SELECT document FROM raw_data WHERE id=3').fetchone()[0])
            row.update(id=10,session_id=meta['session_id'],sequence_id=1)
            row['raw_payload'].update(session_id=meta['session_id'],sequence_id=1)
            db.execute('INSERT INTO raw_data VALUES (?,?,?,?)',(10,meta['session_id'],1,json.dumps(row)))
            row.update(id=11,sequence_id=2)
            row['raw_payload'].update(sequence_id=2)
            row['raw_payload'].pop('runtime')
            db.execute('INSERT INTO raw_data VALUES (?,?,?,?)',(11,meta['session_id'],2,json.dumps(row)))
            db.commit();db.close()
            operation=model.analyze_picos(Path(folder),operation_since='all',progress=lambda *a,**k:None)['picos']['cylinder_01']
            self.assertEqual(operation['status'],'SIMULATION_ONLY')
            self.assertEqual(operation['operation_packets'],1)
            self.assertEqual(operation['excluded_packet_count'],1)
            self.assertEqual(operation['excluded_packets'][0]['raw_id'],11)
            self.assertEqual(operation['excluded_packets'][0]['reason'],'PI_RECEIPT_METADATA_MISSING')
            self.assertEqual(operation['leak_packets'],0)
            self.assertEqual(operation['default_assumptions']['feature'],'OPERATION')
            self.assertIsNone(operation['spectra']['inmp441']['leak_mean_log1p'])
            self.assertIsNotNone(operation['spectra']['inmp441']['operation_mean_log1p'])
            self.assertIsNone(operation['sessions'][-1]['ground_truth'])
            self.assertIsNone(operation['default_result'])
            db=sqlite3.connect(Path(folder)/'pico01-analysis.sqlite3')
            self.assertEqual(db.execute('SELECT count(*) FROM raw_data WHERE id=11').fetchone()[0],1)
            db.execute('DELETE FROM raw_data WHERE id IN (10,11)');db.execute('DELETE FROM sessions WHERE session_id=?',(meta['session_id'],));db.commit();db.close()
            client=model.PicoSQLiteClient(Path(folder),'cylinder_01')
            with self.assertRaises(sqlite3.OperationalError):client.db.execute('DELETE FROM raw_data')
            writer=sqlite3.connect(Path(folder)/'pico01-analysis.sqlite3')
            old=writer.execute('SELECT document FROM raw_data WHERE id=3').fetchone()[0]
            new=json.loads(old);new['id']=4;new['sequence_id']=4
            writer.execute('INSERT INTO raw_data VALUES (?,?,?,?)',(4,new['session_id'],4,json.dumps(new)));writer.commit()
            self.assertEqual(len(client.table('raw_data').select('*').order('id').execute().data),3)
            writer.execute('DELETE FROM raw_data WHERE id=1');writer.commit()
            with self.assertRaises(ValueError):client.verify_source()
            writer.close();client.close()

    def test_browser_switch_clears_other_pico_usage_and_results(self):
        code=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert'),root=process.argv[1],els=new Map();
function el(id){if(!els.has(id))els.set(id,{value:'',textContent:'',children:[],listeners:{},clientWidth:800,width:800,height:280,addEventListener(k,f){this.listeners[k]=f},appendChild(x){this.children.push(x)},getContext(){return new Proxy({},{get:()=>()=>{}})}});return els.get(id)}
const c={Math,Number,Array,Object,Error,Date,URLSearchParams,location:{search:'?pico=cylinder_05'},setTimeout(){},document:{getElementById:el,createElement:()=>({appendChild(){}}),querySelectorAll:()=>['pico','ageMode','priorMeasuredCycles','age','sensor','influence','parameterMode','failures','feature'].map(el)}};c.window=c;c.devicePixelRatio=1;c.addEventListener=()=>{};vm.createContext(c);
vm.runInContext(fs.readFileSync(root+'/public/lifetime-condition-math.js','utf8'),c);vm.runInContext(fs.readFileSync(root+'/public/lifetime-condition-snapshot.js','utf8'),c);
const five=c.LIFETIME_CONDITION_SNAPSHOT.picos?.cylinder_05||c.LIFETIME_CONDITION_SNAPSHOT,one=JSON.parse(JSON.stringify(five));one.cylinder_id='cylinder_01';delete one.video_usage;one.default_assumptions.feature='NORMAL';
c.LIFETIME_CONDITION_SNAPSHOT={status:'PICO_ANALYSIS_COLLECTION',default_cylinder:'cylinder_05',picos:{cylinder_01:one,cylinder_05:five,cylinder_02:{status:'DATA_REQUIRED',cylinder_id:'cylinder_02',message:'정상 기준 필요',source_inventory:{raw_packets:0},generated_at:five.generated_at}}};
el('sensor').value='inmp441';el('parameterMode').value='fit';el('influence').value='.25';vm.runInContext(fs.readFileSync(root+'/public/lifetime-condition-ui.js','utf8'),c);
el('ageMode').value='video';el('priorMeasuredCycles').value='100';el('priorMeasuredCycles').listeners.change();assert.equal(el('error').textContent,'');
el('pico').value='cylinder_01';el('pico').listeners.change();assert(el('source').textContent.includes('cylinder_01'));assert.equal(el('priorMeasuredCycles').value,'');assert.equal(el('ageMode').value,'auto');assert.equal(el('mean').textContent,'입력 확인');
el('pico').value='cylinder_02';el('pico').listeners.change();assert(el('analysisContent').hidden);assert(el('picoState').textContent.includes('정상 기준'));assert.equal(el('mean').textContent,'입력 확인');
el('pico').value='cylinder_05';el('pico').listeners.change();assert(!el('analysisContent').hidden);assert.equal(el('priorMeasuredCycles').value,'');assert(el('videoUsage').textContent.includes('389'));console.log('PICO_SWITCH_PASS');
'''
        completed=subprocess.run(['node','-e',code,str(ROOT)],capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)


if __name__=='__main__':unittest.main()
