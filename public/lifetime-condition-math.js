/* Explicit hypothetical Weibull-AFT scale; conditional remaining life. */
(function(root){
  'use strict';
  function fit(failures){
    if(!Array.isArray(failures)||failures.length<2||failures.some(x=>typeof x!=='number'||!Number.isFinite(x)||x<=0))throw Error('서로 다른 양수 고장 횟수를 2개 이상 입력하세요.');
    const logs=failures.map(Math.log),peak=Math.max(...logs),total=logs.reduce((a,b)=>a+b,0),n=logs.length;
    if(peak-Math.min(...logs)<1e-10)throw Error('고장 횟수가 모두 같습니다.');
    const logPower=k=>peak*k+Math.log(logs.reduce((a,x)=>a+Math.exp(k*x-peak*k),0)/n);
    const objective=k=>n*logPower(k)-n*Math.log(k)-(k-1)*total;
    let lo=.2,hi=50;const q=(Math.sqrt(5)-1)/2;
    for(let i=0;i<160;i++){const a=hi-q*(hi-lo),b=lo+q*(hi-lo);if(objective(a)<objective(b))hi=b;else lo=a;}
    const shape=(lo+hi)/2;if(shape<.2001||shape>49.999)throw Error('고장 횟수의 분포가 지원 형상 범위 밖입니다.');
    return {shape,scale:Math.exp(logPower(shape)/shape)};
  }
  function quantile(age,scale,shape,p){
    if(age===0)return scale*Math.pow(-Math.log(p),1/shape);
    const logX=shape*Math.log(age/scale),c=-Math.log(p);
    if(logX>=Math.log(c))return age*Math.expm1(Math.log1p(c*Math.exp(-logX))/shape);
    return scale*Math.exp((Math.log(c)+Math.log1p(Math.exp(logX-Math.log(c))))/shape)-age;
  }
  function logGamma(z){
    const p=[676.5203681218851,-1259.1392167224028,771.3234287776531,-176.6150291621406,12.507343278686905,-.13857109526572012,9.984369578019572e-6,1.5056327351493116e-7];
    if(z<.5)return Math.log(Math.PI)-Math.log(Math.sin(Math.PI*z))-logGamma(1-z);
    z-=1;let x=.99999999999980993;for(let i=0;i<p.length;i++)x+=p[i]/(z+i+1);const t=z+7.5;
    return .5*Math.log(2*Math.PI)+(z+.5)*Math.log(t)-t+Math.log(x);
  }
  function conditionalMean(age,scale,shape){
    if(age===0)return scale*Math.exp(logGamma(1+1/shape));
    const logX=shape*Math.log(age/scale),s=1/shape;if(logX>35)return Math.exp(Math.log(age)-Math.log(shape)-logX);
    const x=Math.exp(logX);
    if(x<s+1){let total=1/s,term=total;for(let i=1;i<1000;i++){term*=x/(s+i);total+=term;if(Math.abs(term)<Math.abs(total)*1e-14)break;}
      const p=x?total*Math.exp(-x+s*logX-logGamma(s)):0;return Math.exp(Math.log(scale)-Math.log(shape)+x+logGamma(s)+Math.log1p(-p));}
    let b=x+1-s,c=1e300,d=1/b,h=d;for(let i=1;i<1000;i++){const an=-i*(i-s);b+=2;d=an*d+b;c=b+an/c;if(Math.abs(d)<1e-300)d=1e-300;if(Math.abs(c)<1e-300)c=1e-300;d=1/d;const delta=d*c;h*=delta;if(Math.abs(delta-1)<1e-14)break;}
    return age/shape*h;
  }
  function evaluate({age,distance,influence,scale,shape}){
    if([age,distance,influence,scale,shape].some(x=>typeof x!=='number'||!Number.isFinite(x)))throw Error('모든 가정값을 숫자로 입력하세요.');
    if(!(age>=0&&age<=1e7&&distance>=0&&distance<=100&&influence>=0&&influence<=5&&scale>=1&&scale<=1e8&&shape>=.2&&shape<=50))throw Error('입력값이 지원 계산 범위 밖입니다.');
    const effectiveScale=scale*Math.exp(-influence*distance);
    if(effectiveScale<1e-6)throw Error('가정한 소리 영향이 너무 큽니다. 영향 계수를 줄이세요.');
    function survival(extra){
      if(extra<=0)return 1;
      let hazard;
      if(age===0)hazard=Math.pow(extra/effectiveScale,shape);
      else{const logH=shape*Math.log(age/effectiveScale),delta=shape*Math.log1p(extra/age),logDifference=delta>50?delta:Math.log(Math.expm1(delta)),logHazard=logH+logDifference;if(logHazard>Math.log(745))return 0;hazard=Math.exp(logHazard);}
      return hazard<745?Math.exp(-hazard):0;
    }
    const median=quantile(age,effectiveScale,shape,.5),upper=quantile(age,effectiveScale,shape,1e-10);
    const mean=conditionalMean(age,effectiveScale,shape);
    return {status:'SIMULATION_ONLY',assumed_age_cycles:age,condition_distance:distance,assumed_sound_influence:influence,
      baseline_scale:scale,effective_scale:effectiveScale,shape,mean_remaining_cycles:mean,median_remaining_cycles:median,
      mean_total_cycles:age+mean,survival:Array.from({length:101},(_,i)=>({extra_cycles:upper*i/100,probability:survival(upper*i/100)})),
      formula:'S(age+extra|D)/S(age|D); scale(D)=scale0*exp(-assumed_influence*D)'};
  }
  function condition({referenceMean,shape,distance,influence}){
    if([referenceMean,shape,distance,influence].some(x=>typeof x!=='number'||!Number.isFinite(x)))throw Error('수명 기준과 소리 영향값을 숫자로 입력하세요.');
    if(!(referenceMean>=1&&referenceMean<=1e8&&shape>=.2&&shape<=50&&distance>=0&&distance<=100&&influence>=0&&influence<=5))throw Error('수명 기준 또는 소리 영향값이 지원 범위 밖입니다.');
    const baselineScale=referenceMean/Math.exp(logGamma(1+1/shape)),scale=baselineScale*Math.exp(-influence*distance);
    if(scale<1e-6)throw Error('소리 영향이 너무 큽니다. 영향 계수를 줄이세요.');
    const mean=scale*Math.exp(logGamma(1+1/shape)),upper=scale*Math.pow(-Math.log(1e-6),1/shape);
    return {status:'SIMULATION_ONLY',reference_mean_cycles:referenceMean,shape,assumed_sound_influence:influence,
      condition_distance:distance,effective_scale:scale,mean_life_cycles:mean,median_life_cycles:scale*Math.pow(Math.log(2),1/shape),
      change_from_reference_percent:100*(mean/referenceMean-1),actual_age_cycles:null,remaining_cycles:null,
      survival:Array.from({length:81},(_,i)=>({cycles:upper*i/80,probability:Math.exp(-Math.pow((upper*i/80)/scale,shape))})),
      formula:'mean(D)=reference_mean*exp(-assumed_gamma*D); unconditional total-life scenario, not remaining life'};
  }
  const paperFailures=[3500000,5000000,6000000,8000000,10000000];
  const videoSessions=['c1a75b49-3852-4b08-9dfb-647121e15d2d','2f5c7571-b033-4423-b7bd-9cb9bc99fca2','2f138a41-621d-46e3-bfd2-d87dfe1f8498','4bd5133d-2ffa-4071-8d76-5916c7dcb398','39573bde-c929-4612-a19f-cb81a6b3b2ae','0b82f226-b803-4c35-b287-e357bc3fc167'];
  function pressureDistribution(beta,eta){
    return {beta,eta_cycles:eta,mean_cycles:eta*Math.exp(logGamma(1+1/beta)),median_cycles:eta*Math.pow(Math.log(2),1/beta),b10_cycles:eta*Math.pow(-Math.log(.9),1/beta)};
  }
  function pressureReliability(cycles,beta,eta){
    if(![cycles,beta,eta].every(x=>typeof x==='number'&&Number.isFinite(x))||cycles<0||beta<=0||eta<=0)throw Error('유효한 왕복 위치와 Weibull 모수가 필요합니다.');
    if(cycles===0)return {cycles:0,reliability:1,failure_probability:0};
    const logHazard=beta*Math.log(cycles/eta),hazard=logHazard<Math.log(745)?Math.exp(logHazard):Infinity;
    return {cycles,reliability:Math.exp(-hazard),failure_probability:-Math.expm1(-hazard)};
  }
  function pressureTimeHours(cycles,scope){
    if(typeof cycles!=="number"||!Number.isFinite(cycles)||cycles<0)throw Error("유효한 왕복 횟수가 필요합니다.");
    if(!scope||scope.cylinder_id!=='cylinder_05'||scope.physical_cylinder_id!=='CYL-005'||scope.same_speed_confirmed!==true||
      !Array.isArray(scope.session_ids)||scope.session_ids.length!==6||new Set(scope.session_ids).size!==6||
      !scope.session_ids.every(id=>videoSessions.includes(id))||!Number.isFinite(scope.period_seconds)||Math.abs(scope.period_seconds-2.6016069444)>1e-10)return null;
    return cycles*2.6016069444/3600;
  }
  function pressureScenario({failures=paperFailures,paperPressure=1.2,targetPressure=.2,exponent=1,timeScope=null}={}){
    if(!Array.isArray(failures)||failures.length!==5||failures.some(v=>typeof v!=='number'||!Number.isFinite(v)||v<=0||!Number.isInteger(v)))throw Error('고장 시편별 양의 정수 횟수 5개를 입력하세요. 평균 하나를 시편으로 넣지 않습니다.');
    if(![paperPressure,targetPressure,exponent].every(v=>typeof v==='number'&&Number.isFinite(v))||paperPressure<=0||targetPressure<=0||exponent<0||exponent>10)throw Error('양의 압력과 0~10 범위의 가정 지수 m을 입력하세요.');
    const factor=Math.pow((paperPressure*1000000)/(targetPressure*1000000),exponent),mock=failures.map(v=>v*factor);
    if(!Number.isFinite(factor)||mock.some(v=>!Number.isFinite(v)||v<=0))throw Error('환산 횟수가 지원 범위를 벗어났습니다.');
    const logs=mock.map(Math.log),average=logs.reduce((a,b)=>a+b,0)/logs.length,centered=logs.map(v=>v-average),peak=Math.max(...centered);
    if(Math.max(...logs)-Math.min(...logs)<1e-12)throw Error('고장 횟수 5개가 모두 같습니다.');
    function score(beta){const w=centered.map(v=>Math.exp(beta*(v-peak)));return 1/beta-w.reduce((sum,v,i)=>sum+v*centered[i],0)/w.reduce((a,b)=>a+b,0);}
    let lo=1e-6,hi=1;while(score(hi)>0&&hi<1e6)hi*=2;
    if(score(hi)>0)throw Error('Weibull 형상모수를 적합할 수 없습니다.');
    for(let i=0;i<160;i++){const mid=(lo+hi)/2;if(score(mid)>0)lo=mid;else hi=mid;}
    const beta=(lo+hi)/2,eta=Math.exp(average+peak+Math.log(centered.reduce((sum,v)=>sum+Math.exp(beta*(v-peak)),0)/logs.length)/beta),weibull=pressureDistribution(beta,eta);
    const upper=eta*Math.pow(-Math.log(1e-6),1/beta);
    return {status:'SIMULATION_ONLY',version:'woo2018_pressure_five_failure_mle_v1',
      paper:{url:'https://www.davidpublisher.com/Public/uploads/Contribute/5c3465f825c7e.pdf',year:2018,company:'A',pressure_mpa:1.2,temperature_c:23,bore_mm:32,stroke_mm:160,cycle_seconds:1,total_specimens:6,observed_failures:5,censored_end_cycles:null,failure_cycles:paperFailures.slice()},
      scenario_input_failure_cycles:failures.slice(),assumptions:{paper_pressure_mpa:paperPressure,target_pressure_mpa:targetPressure,pressure_exponent:exponent,pressure_factor:factor,stroke_ratio_correction_applied:false,source_values_edited:paperPressure!==1.2||failures.some((v,i)=>v!==paperFailures[i])},
      mock_failure_cycles:mock,weibull:{...weibull,method:'two_parameter_location_zero_complete_failure_mle_profile_score',location:0,censored_observations_used:0},
      simplification:{total_specimens_reported:6,failed_used:5,censoring_used:false,unfailed_endpoint_cycles:null},
      time_conversion:{scope:timeScope,mean_operating_hours:pressureTimeHours(weibull.mean_cycles,timeScope),b10_operating_hours:pressureTimeHours(weibull.b10_cycles,timeScope),speed_wear_correction_applied:false,calendar_years:null},
      curves:Array.from({length:101},(_,i)=>pressureReliability(upper*i/100,beta,eta))};
  }
  function pressureHypothesis(base,distance,coefficient=null){
    if(typeof distance!=='number'||!Number.isFinite(distance)||distance<0)throw Error('유효한 STFT 정규화 편차가 필요합니다.');
    if(coefficient===null)return {status:'ASSUMPTION_NOT_SELECTED',coefficient:null,condition_distance:distance,mean_life_cycles:null,actual_age_cycles:null,remaining_cycles:null};
    if(typeof coefficient!=='number'||!Number.isFinite(coefficient)||coefficient<0||coefficient>5)throw Error('사용자가 선택한 가정 계수 k를 0~5 범위로 입력하세요.');
    const beta=base.weibull.beta,eta=base.weibull.eta_cycles*Math.exp(-coefficient*distance);
    if(eta<1e-6)throw Error('가정한 STFT 영향이 너무 큽니다.');
    const d=pressureDistribution(beta,eta),upper=eta*Math.pow(-Math.log(1e-6),1/beta);
    return {status:'SIMULATION_ONLY',label:'STFT 상태를 반영한 가설 평균수명',coefficient,coefficient_source:'EXPLICIT_USER_ASSUMPTION_NOT_LEARNED',condition_distance:distance,beta_fixed:true,shape:beta,effective_scale:eta,
      mean_life_cycles:d.mean_cycles,median_life_cycles:d.median_cycles,b10_life_cycles:d.b10_cycles,reference_mean_cycles:base.weibull.mean_cycles,actual_age_cycles:null,remaining_cycles:null,mean_operating_hours:null,b10_operating_hours:null,
      survival:Array.from({length:101},(_,i)=>pressureReliability(upper*i/100,beta,eta))};
  }
  const api={fit,evaluate,condition,pressureScenario,pressureHypothesis,pressureReliability,pressureTimeHours};root.LifetimeConditionMath=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
