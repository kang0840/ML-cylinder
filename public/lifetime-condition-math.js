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
  const api={fit,evaluate};root.LifetimeConditionMath=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
