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
const ids=['parameterMode','ageMode','age','influence','period','prior','sensor','shape','scale','failures','feature'];
const c={console,Math,Number,Array,Object,Error,Date,URLSearchParams,location:{search:''},setTimeout(){},document:{getElementById:element,createElement:()=>({appendChild(){}}),querySelectorAll:()=>ids.map(element)}};
c.window=c;c.devicePixelRatio=1;c.addEventListener=()=>{};
vm.createContext(c);for(const f of ['lifetime-condition-math.js','lifetime-condition-snapshot.js','lifetime-condition-ui.js'])vm.runInContext(fs.readFileSync(root+'/public/'+f,'utf8'),c);
assert(element('error').textContent.includes('누적 사용량 미상'));assert.equal(element('mean').textContent,'입력 확인');
element('age').value='100';element('age').listeners.change();assert.equal(element('error').textContent,'');assert(element('used').textContent.includes('실측 입력'));
element('age').value='100.5';element('age').listeners.change();assert(element('error').textContent.includes('정수'));assert.equal(element('mean').textContent,'입력 확인');
element('ageMode').value='operation';element('ageMode').listeners.change();assert(element('error').textContent.includes('생성하지 않습니다'));assert.equal(element('mean').textContent,'입력 확인');
element('reset').listeners.click();assert(element('error').textContent.includes('누적 사용량 미상'));assert.equal(element('age').value,'');
element('age').value='100';
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
        self.assertIsNone(data['default_assumptions']['age_cycles'])
        self.assertIsNone(data['default_result'])


if __name__=='__main__':unittest.main()
