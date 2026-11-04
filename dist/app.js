(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const label = {D:'日线', W:'周线', M:'月线'};
  const safe = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (v,n=2) => v==null?'—':v.toLocaleString('zh-CN',{minimumFractionDigits:n,maximumFractionDigits:n});
  const score = b => b?.[4] ? [b[4].slice(0,4).reduce((a,x)=>a+x,0),b[4].slice(4).reduce((a,x)=>a+x,0)] : [null,null];
  const namesP=['零轴上方趋势','正柱强度','近三期动能改善','最近五期金叉'], maxP=[30,25,25,20];
  const namesR=['零轴下方趋势','负柱强度','近三期动能减弱','最近五期死叉','偏离较大且动能转弱'], maxR=[25,20,25,15,15];
  const state={p:'D', length:60, scale:'points', end:'', partial:false, search:'', sort:'catalog'};
  let data=window.SECTOR_SNAPSHOT, win, activeRows=[], index=new Map(), visible=new Set(), cursor=-1, locked=false, sharedMax=1, version='', statusMsg='', statusKey='', liveExpected='', refreshTimer, detailCode=null;
  const online=/^https?:$/.test(location.protocol);
  const tip=document.createElement('div'); tip.className='tooltip'; tip.hidden=true; document.body.append(tip);
  const list=$('list');
  if(!data || !data.rows?.length){
    list.innerHTML='<p class="empty">未找到本地行情快照。请先运行「更新数据」，再重新打开观察台。</p>';
    return;
  }

  function windowFor(){
    const axis=data.axes[state.p], limit=state.end || axis.dates.at(-1);
    const valid=[]; let si=-1;
    axis.dates.forEach((d,i)=>{
      if(d>limit)return;
      if(axis.closed[i])si=i;
      if(axis.closed[i] || (state.partial && state.p!=='D' && i===axis.dates.length-1))valid.push(i);
    });
    const ids=valid.slice(-state.length);
    const goal=(!state.end && data.goals[state.p]) || axis.dates[si];
    return {axis,ids,si,goal,match:si>=0 && axis.dates[si]===goal};
  }
  function scoringBar(row){return win.match ? row.series[state.p][win.si] : null;}
  function trend(b,prev){
    const h=b?.[3], ph=prev?.[3];
    if(h==null)return ['暂无指标',''];
    if(h===0)return ['柱体持平',''];
    if(ph==null)return h>0?['正柱','bull']:['负柱','bear'];
    if(h>0)return [ph<=0?'刚转红柱':h>ph?'红柱扩张':h<ph?'红柱收窄':'红柱持平','bull'];
    return [ph>=0?'刚转绿柱':h<ph?'绿柱扩张':h>ph?'绿柱收窄':'绿柱持平','bear'];
  }
  function barLabel(row){
    const b=scoringBar(row);
    if(!b)return row.last && win.goal && row.last<win.goal?'数据滞后':'当期缺数据';
    if(b[1]==null)return '指标预热中';
    return trend(b,row.series[state.p][win.si-1])[0];
  }
  function groups(){
    const axis=win.axis, s=state.p;
    $('endDate').min=axis.dates[0]; $('endDate').max=data.axes.D.dates.at(-1);
    $('endDate').value=state.end || data.axes.D.dates.at(-1);
    $('latestBtn').hidden=!state.end;
    $('partialWrap').hidden=s==='D'; $('partial').checked=state.partial;
    document.querySelectorAll('[data-p]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.p===s)));
    $('axisRange').textContent=win.ids.length?axis.dates[win.ids[0]]+' → '+axis.dates[win.ids.at(-1)]+' · '+win.ids.length+' 期':'本段无数据';
    const scoreDay=win.goal || '—';
    $('breadthPeriod').textContent=label[s]+' · '+scoreDay+' 已收盘';
    let pos=0,neg=0,flat=0,missing=0;
    for(const row of data.rows){
      const h=scoringBar(row)?.[3];
      if(h==null)missing++;else if(h>0)pos++;else if(h<0)neg++;else flat++;
    }
    const rest=flat+missing;
    for(const [k,n] of [['up',pos],['down',neg],['flat',rest]]){
      $(k+'Count').textContent=n; $(k+'Meter').style.width=(n/data.count*100)+'%';
    }
    $('flatCount').title='零柱 '+flat+' 个；缺值或预热 '+missing+' 个';
    const behind=liveExpected && liveExpected>data.asOf;
    $('asof').innerHTML='实际收盘 <strong>'+safe(data.asOf)+'</strong> · '+(behind?'新收盘尚未到齐':data.expectedDay ? data.fresh+' / '+data.count+' 行业到齐' : data.count+' 个行业，交易日历待核实');
    document.querySelector('.verified-dot').classList.toggle('warning-dot',!!behind||data.fresh!==data.count);
    let msg='';
    if(data.expectedDay && data.fresh<data.count)msg='截至应有收盘 '+data.expectedDay+'，仅 '+data.fresh+'/'+data.count+' 个行业到齐；缺失位置留空，不沿用上一期评分。';
    if(!data.expectedDay)msg='交易日历暂未覆盖当前日期。这里只显示已收到的行情，请核对最新交易日。';
    if(state.partial && s!=='D' && win.ids.length && !axis.closed[win.ids.at(-1)])msg+=' 图表末列的虚线区域是未收盘期，分布和评分仍截至 '+scoreDay+'。';
    if(statusMsg)msg+=' '+statusMsg;
    $('notice').hidden=!msg.trim(); $('notice').textContent=msg.trim();
  }
  function scoreRow(name,value,cls=''){
    return `<div class="score-row ${cls}"><span>${name}</span><span class="meter"><i style="width:${value??0}%"></i></span><b>${value??'—'}</b></div>`;
  }
  function sectorHtml(row,ranking){
    const b=scoringBar(row), ns=score(b), flag=barLabel(row), side=b?.[3]>0?'bull':b?.[3]<0?'bear':'';
    return `<article class="sector" data-code="${row.code}" aria-label="${safe(row.name)} MACD">
      <div class="identity"><div class="sector-code"><b>${String(ranking+1).padStart(2,'0')}</b><span>THS · ${row.code}</span></div><h3>${safe(row.name)}</h3>
      <div class="signal ${side}">${safe(flag)}</div><div class="close-line">收盘 <span class="num">${fmt(b?.[0])}</span></div></div>
      <div class="chart" tabindex="0" role="group" aria-label="${safe(row.name)}${label[state.p]} MACD，使用左右键对齐日期，回车锁定"><span class="cross"></span></div>
      <div class="scores"><div class="score-caption">${label[state.p]}评分 <span>/ 100</span></div>${scoreRow('潜力值',ns[0])}${scoreRow('风险值',ns[1],'risk')}
      ${ns[0]==null?'<div class="noscore">'+(b?'历史不足 52 期':'缺本期收盘')+'</div>':''}
      <button type="button" class="interpret" data-detail="${row.code}">查看评分依据 <span class="arr">↗</span></button></div></article>`;
  }

  // All panels use the exact same x slots. Empty observations break lines and leave empty bars.
  const L=44,R=10,T=9,B=20;
  const normalized=(b,j)=>state.scale==='percent'?100*b[j]/b[0]:b[j];
  function findSharedMax(){
    let v=0;
    if(state.scale==='percent')for(const row of data.rows)for(const i of win.ids){
      const b=row.series[state.p][i]; if(b?.[1]==null)continue;
      for(let j=1;j<=3;j++)v=Math.max(v,Math.abs(normalized(b,j)));
    }
    return v ? v*1.06 : 1;
  }
  function draw(chart){
    const row=index.get(chart.parentElement.dataset.code), a=row.series[state.p], ids=win.ids;
    const w=chart.clientWidth,h=chart.clientHeight;
    if(w<10 || !h)return;
    const avail=w-L-R, gh=h-T-B, n=ids.length;
    const xp=j=>L+avail*(j+.5)/Math.max(1,n), paths=['',''];
    let hi=0,lo=0,seen=0;
    for(const i of ids){
      const b=a[i];if(b?.[1]==null)continue;seen++;
      for(let k=1;k<=3;k++){const v=normalized(b,k);hi=Math.max(hi,v);lo=Math.min(lo,v);}
    }
    if(state.scale==='percent'){hi=sharedMax;lo=-sharedMax;}
    else{const span=hi-lo||1;hi+=span*.09;lo-=span*.09;}
    const yp=v=>T+(hi-v)/(hi-lo)*gh, num=x=>Number(x.toFixed(2)), zero=yp(0), wid=Math.max(.8,avail/Math.max(n,1)*.64);
    let back='',front='', dif='',dea='',dashedD='',dashedE='', prev=null;
    const yTick=v=>state.scale==='percent'?(Math.abs(v)>=10?v.toFixed(0):v.toFixed(1))+'%':Math.abs(v)>=1000?(v/1000).toFixed(1)+'k':Math.abs(v)>=10?v.toFixed(0):v.toFixed(1);
    for(const v of [hi,0,lo]){
      const y=yp(v);
      if(v!==0 && Math.abs(y-zero)<12)continue;
      back+=`<path d="M${L},${num(y)}H${w-R}" stroke="${v===0?'#b8c3b5':'#e0e5da'}" ${v===0?'':'stroke-dasharray="2 4"'}/><text x="${L-6}" y="${num(y+3)}" text-anchor="end">${v===0?'0':yTick(v)}</text>`;
    }
    const nt=w<380?3:5;
    for(const j of new Set(Array.from({length:nt},(_,k)=>Math.round((n-1)*k/(nt-1))))){
      if(j<0 || j>=n)continue;
      const x=xp(j),dt=win.axis.dates[ids[j]],lab=state.p==='M'?dt.slice(0,7):(w<550 && j>0?dt.slice(5):dt);
      back+=`<path d="M${num(x)},${T}V${h-B}" stroke="#e0e5da" stroke-dasharray="2 4"/>`;
      front+=`<text x="${num(x)}" y="${h-3}" text-anchor="${j===0?'start':j===n-1?'end':'middle'}">${lab}</text>`;
    }
    ids.forEach((idx,j)=>{
      const b=a[idx], x=xp(j), isFinal=win.axis.closed[idx];
      if(!isFinal)back+=`<rect x="${num(x-avail/n/2)}" y="${T}" width="${num(avail/n)}" height="${gh}" fill="#f4eace"/><path d="M${num(x-avail/n/2)},${T}v${gh}" stroke="#a8823d" stroke-dasharray="2 3"/>`;
      if(b?.[1]==null){prev=null;return;}
      const hist=normalized(b,3), base=Math.min(zero,yp(hist)),bh=Math.abs(yp(hist)-zero), px=num(x),pyD=num(yp(normalized(b,1))),pyE=num(yp(normalized(b,2)));
      const pal=hist>=0 ? 0 : 1;
      paths[pal]+=`M${num(x-wid/2)} ${num(base)}h${num(wid)}v${num(Math.max(.5,bh))}h-${num(wid)}z`;
      const dc=px+' '+pyD, ec=px+' '+pyE;
      if(isFinal){dif+=(prev?'L':'M')+dc;dea+=(prev?'L':'M')+ec;}
      else if(prev){dashedD+='M'+prev[0]+'L'+dc;dashedE+='M'+prev[1]+'L'+ec;}
      prev=[dc,ec];
    });
    const lines=(d,color,dash='')=>`<path d="${d}" fill="none" stroke="${color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" ${dash}/>`;
    chart.innerHTML=`<svg aria-hidden="true" viewBox="0 0 ${w} ${h}" font-family="Consolas,monospace" font-size="9" fill="#748073">${back}<path d="${paths[0]}" fill="#cf7966"/><path d="${paths[1]}" fill="#6e9d89"/>${lines(dif,'#2e6596')}${lines(dea,'#b8822b')}${lines(dashedD,'#2e6596','stroke-dasharray="3 3"')}${lines(dashedE,'#b8822b','stroke-dasharray="3 3"')}${front}</svg><span class="cross"></span>${seen?'':'<span class="chart-note">此窗口无足够历史指标</span>'}`;
  }
  const watch=new IntersectionObserver(entries=>{for(const e of entries){const chart=e.target;if(e.isIntersecting){visible.add(chart);draw(chart);}else visible.delete(chart);}},{rootMargin:'350px 0px'});
  let lastWidth=0;
  new ResizeObserver(es=>{const w=es[0].contentRect.width;if(w!==lastWidth){lastWidth=w;visible.forEach(draw);}}).observe(list);
  function render(){
    win=windowFor(); index=new Map(data.rows.map(r=>[r.code,r]));
    watch.disconnect(); visible.clear(); locked=false;setCursor(-1);
    groups();sharedMax=findSharedMax();
    const q=state.search.trim().toLowerCase();activeRows=data.rows.filter(r=>!q||r.name.toLowerCase().includes(q)||r.code.includes(q));
    if(state.sort!=='catalog'){
      const k=state.sort==='risk'?1:0;
      activeRows.sort((a,b)=>(score(scoringBar(b))[k]??-1)-(score(scoringBar(a))[k]??-1));
    }
    $('rowCount').textContent=activeRows.length+' / '+data.count;
    list.innerHTML=activeRows.length?activeRows.map(sectorHtml).join(''):'<div class="empty">没有匹配的行业。请换一个行业名称或六位代码。</div>';
    list.querySelectorAll('.chart').forEach(c=>watch.observe(c));
  }

  function setCursor(j){
    cursor=j;list.classList.toggle('has-cursor',j>=0);
    if(j<0){tip.hidden=true;$('pinned').textContent='';return;}
    const frac=(j+.5)/win.ids.length;
    list.style.setProperty('--cross-left',`calc(${frac*100}% + ${L-frac*(L+R)}px)`);
    $('pinned').textContent=(locked?'已锁定 ':'对齐 ')+win.axis.dates[win.ids[j]]+(locked?' · Esc 解除':'');
  }
  function nearest(chart,x){const r=chart.getBoundingClientRect();return Math.max(0,Math.min(win.ids.length-1,Math.floor((x-r.left-L)/(r.width-L-R)*win.ids.length)));}
  function tooltip(chart,j,x,y){
    if(j<0)return;
    const row=index.get(chart.parentElement.dataset.code),i=win.ids[j],b=row.series[state.p][i],dt=win.axis.dates[i],done=win.axis.closed[i];
    tip.innerHTML=`<strong>${safe(row.name)} · ${dt}${done?'':'（未收盘期）'}</strong><br>收盘 ${fmt(b?.[0],3)}　柱 ${fmt(b?.[3],3)}<br>DIF ${fmt(b?.[1],3)}　DEA ${fmt(b?.[2],3)}`;
    if(state.scale==='percent' && b?.[3]!=null)tip.innerHTML+='<br>柱 / 收盘 '+fmt(100*b[3]/b[0],3)+'%';
    tip.hidden=false;
    tip.style.left=Math.max(4,Math.min(innerWidth-tip.offsetWidth-6,x+14))+'px';
    tip.style.top=Math.max(4,y+tip.offsetHeight+15>innerHeight?y-tip.offsetHeight-8:y+12)+'px';
  }
  list.addEventListener('pointermove',e=>{const chart=e.target.closest('.chart');if(!chart||!win.ids.length)return;const j=locked?cursor:nearest(chart,e.clientX);if(cursor!==j)setCursor(j);tooltip(chart,j,e.clientX,e.clientY);});
  list.addEventListener('pointerout',e=>{if(e.target.closest('.chart') && e.target.closest('.chart')!==e.relatedTarget?.closest?.('.chart')){tip.hidden=true;if(!locked)setCursor(-1);}});
  list.addEventListener('click',e=>{
    const detail=e.target.closest('[data-detail]');if(detail){openDetail(detail.dataset.detail);return;}
    const chart=e.target.closest('.chart');if(!chart||!win.ids.length)return;
    locked=!locked;const j=nearest(chart,e.clientX);setCursor(j);tooltip(chart,j,e.clientX,e.clientY);
  });
  list.addEventListener('keydown',e=>{
    const chart=e.target.closest('.chart');if(!chart||!win.ids.length)return;
    if(!['ArrowLeft','ArrowRight','Enter'].includes(e.key))return;e.preventDefault();
    if(e.key==='Enter')locked=!locked;
    const j=cursor<0?win.ids.length-1:Math.max(0,Math.min(win.ids.length-1,cursor+(e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0)));
    setCursor(j);const rect=chart.getBoundingClientRect();tooltip(chart,j,rect.left+rect.width*.5,rect.top+rect.height*.65);
  });
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){locked=false;setCursor(-1);}});
  window.addEventListener('scroll',()=>{tip.hidden=true;},{passive:true});
  $('periods').addEventListener('click',e=>{if(!e.target.dataset.p)return;state.p=e.target.dataset.p;
    if(state.end && state.end<data.axes[state.p].dates[0])state.end=data.axes[state.p].dates[0];render();});
  $('length').onchange=e=>{state.length=Number(e.target.value);render();};$('scale').onchange=e=>{state.scale=e.target.value;render();};
  $('endDate').onchange=e=>{const v=e.target.value;if(v && !e.target.validity.valid)return;state.end=v===data.axes.D.dates.at(-1)?'':v;render();};
  $('latestBtn').onclick=()=>{state.end='';render();};$('partial').onchange=e=>{state.partial=e.target.checked;render();};
  $('search').oninput=e=>{state.search=e.target.value;render();};$('sort').onchange=e=>{state.sort=e.target.value;render();};
  $('toTop').onclick=e=>{e.preventDefault();window.scrollTo({top:0,behavior:'smooth'});};

  function showDialog(title,kicker,body){
    tip.hidden=true;$('dialogTitle').textContent=title;$('dialogKicker').textContent=kicker;$('dialogBody').innerHTML=body;
    if(!$('dialog').open)$('dialog').showModal();$('dialog').scrollTop=0;
  }
  $('closeDialog').onclick=()=>$('dialog').close();
  $('dialog').addEventListener('click',e=>{if(e.target===$('dialog'))$('dialog').close();});
  $('dialog').addEventListener('close',()=>{detailCode=null;});
  function detailParts(title,values,names,caps,cls){
    return `<div class="score-detail ${cls}"><div class="total"><span>${title}</span><b>${values.reduce((a,b)=>a+b,0)} / 100</b></div>${values.map((v,i)=>'<div><span>'+names[i]+'</span><b>'+v+' / '+caps[i]+'</b></div>').join('')}</div>`;
  }
  function horizons(row){
    const until=state.end || data.axes.D.dates.at(-1), signs=[], table=[];
    for(const p of ['D','W','M']){
      const axis=data.axes[p];
      let i=axis.dates.length-1;
      while(i>=0 && (axis.dates[i]>until || !axis.closed[i]))i--;
      const day=i>=0?axis.dates[i]:null;
      const b=(!state.end && data.goals[p] && data.goals[p]!==day)?null:row.series[p][i];
      const h=b?.[3], pair=score(b), [tag,cls]=trend(b,row.series[p][i-1]);
      signs.push(h==null?null:Math.sign(h));
      table.push(`<tr><th scope="row">${label[p]}</th><td>${day||'—'}</td><td><span class="signal ${cls}">${tag}</span></td><td>${pair[0]??'—'}</td><td>${pair[1]??'—'}</td></tr>`);
    }
    const [d,w,m]=signs;
    const note=signs.includes(null)?'有周期缺少已确认的指标，暂不判断三个周期是否一致。':
      signs.every(x=>x>0)?'三个周期均为红柱，DIF 均高于各自的信号线。还要看柱体是在扩张还是收窄。':
      signs.every(x=>x<0)?'三个周期均为绿柱，DIF 均低于各自的信号线。绿柱收窄说明压力减轻，但尚未翻红。':
      d>0 && w<0 && m<0?'日线为红柱，周线和月线仍为绿柱。短期动能较强，尚未得到两个较长周期的确认。':
      d<0 && w>0 && m>0?'日线为绿柱，周线和月线仍为红柱。短期动能回落，较长周期的正动能仍在。':
      '三个周期没有一致的红柱或绿柱；应按你关注的观察周期解读，不把三个分数简单平均。';
    return `<h3>同一截止日 · 三周期对照</h3><table class="horizons"><thead><tr><th>周期</th><th>已收盘至</th><th>柱体</th><th>潜力</th><th>风险</th></tr></thead><tbody>${table.join('')}</tbody></table><p>${note}</p>`;
  }
  function openDetail(code){
    detailCode=code;const row=index.get(code),b=scoringBar(row),prev=row.series[state.p][win.si-1];
    let body=`<p>评分基准：<b>${safe(win.goal??'—')} · ${label[state.p]}已收盘</b>。该指数原始收盘记录：${safe(row.first??'—')} 至 ${safe(row.last??'—')}。</p>`;
    body+=horizons(row);
    if(!b?.[4])body+='<p>所选周期没有已核实的收盘，或连续历史不足 52 期，暂不给分。</p>';
    else{
      const [tag]=trend(b,prev), trendText=b[1]>0?'快均线位于慢均线上方':b[1]<0?'快均线位于慢均线下方':'快慢均线持平';
      body+=`<h3>当前怎么看</h3><p>${trendText}，${tag}。DIF 为 <b>${fmt(b[1],3)}</b>，DEA 为 <b>${fmt(b[2],3)}</b>，柱值 <b>${fmt(b[3],3)}</b>${prev?.[3]!=null?'（上期 '+fmt(prev[3],3)+'）':''}。</p>`;
      if(b[4][8])body+=b[1]>0?'<p>快线高于慢线的幅度处于近期高位，但柱体较三期前转弱；即使趋势仍强，回落风险也会加分。</p>':'<p>快线已明显低于慢线，且柱体动能较三期前进一步走弱，弱势延续的风险加分。</p>';
      if(b[1]<0 && b[4][2]>0)body+='<p>零轴下方已出现动能修复，弱势趋势的风险分仍保留。</p>';
      body+=detailParts('潜力值',b[4].slice(0,4),namesP,maxP,'p')+detailParts('风险值',b[4].slice(4),namesR,maxR,'r');
    }
    body+=`<p>高分表示相应的技术特征更强，不代表未来涨跌概率。<a class="source" href="https://q.10jqka.com.cn/thshy/detail/code/${row.code}/" target="_blank" rel="noreferrer">核对同花顺行业指数 ↗</a></p>`;
    showDialog(row.name+' · 评分依据','SIGNAL NOTES   /   THS '+code,body);
  }
  $('methodBtn').onclick=()=>showDialog('读图与评分','FIELD GUIDE   /   使用说明',
    '<h3>让同一日期纵向对齐</h3><p>用鼠标滚轮浏览行业。移入任意图表，同一天的参考线会出现在所有图上；点击锁定，可滚动到其他行业比较，按 Esc 解除。各行业点数模式分别缩放；切换「统一百分比」，三条数值均除以同期收盘，90 个行业共用同一纵轴。</p>'+
    '<h3>柱线从哪里来</h3><div class="formula">DIF = EMA(收盘, 12) − EMA(收盘, 26)<br>DEA = EMA(DIF, 9)<br>柱值 = 2 × (DIF − DEA)</div><p>蓝线是 DIF，金线是 DEA；正柱为红，负柱为绿。柱色表示快慢信号的相对位置，并不等于当期价格涨跌。周线、月线用该期最后交易日的行业指数收盘独立计算。EMA 以首个收盘初始化，完整历史先预热；连续满 26 期绘线、52 期评分，缺失期不连线。当前未收盘的周或月可以预览，不计分。</p>'+
    '<h3>潜力和风险独立打分，各 0–100</h3><p>潜力：零轴上方趋势 30、正柱强度 25、近三期动能改善 25、金叉 20。风险：零轴下方趋势 25、负柱强度 20、近三期动能减弱 25、死叉 15、偏离较大且动能转弱 15。两者可以同时有分；风险值为 0 也不等于没有投资风险。点击行业右侧可查看各项得分。</p>'+
    '<p>先把 DIF 和柱除以当期收盘；动能变化取归一柱与三期前的差。各强度项按对应方向除以前 26 期绝对值的 75% 分位，最高取满分。交叉当期得满分，接下来四期线性递减，再归零。DIF 的绝对幅度达到前 26 期绝对值的 80% 分位且动能转弱，加风险 15 分，兼顾高位回落和深度弱势。历史日期的指标和得分仅按截至该日的价格计算。</p>'+
    '<h3>数据与更新</h3><p>收盘来自<a class="source" href="https://q.10jqka.com.cn/thshy/" target="_blank" rel="noreferrer">同花顺行业指数</a>，交易日依照<a class="source" href="https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month=2026-11" target="_blank" rel="noreferrer">深交所日历</a>。本地程序安排北京时间 16:15 检查更新。数据源延迟时保留旧快照，页面标注实际日期。技术评分未按未来收益校准，不覆盖估值、基本面、政策或流动性风险。</p>');

  function reloadData(tag){
    const sc=document.createElement('script');sc.src='market.js?v='+encodeURIComponent(tag);
    sc.onload=()=>{data=window.SECTOR_SNAPSHOT;version=tag;const detail=detailCode;render();if(detail)openDetail(detail);sc.remove();};
    sc.onerror=()=>{statusMsg='快照读取失败，继续使用上一版。';groups();sc.remove();};document.head.append(sc);
  }
  async function poll(method='GET'){
    clearTimeout(refreshTimer);
    try{
      const resp=await fetch(method==='POST'?'/api/refresh':'/api/status',{method,cache:'no-store',credentials:'same-origin'});
      if(!resp.ok)throw Error('HTTP '+resp.status);
      const st=await resp.json();if(st.app!=='industry-macd')throw Error('无法识别本地服务');
      $('refreshBtn').hidden=false;$('refreshBtn').disabled=st.running;$('refreshBtn').textContent=st.running?'↻ 正在更新…':'↻ 更新数据';
      $('modeLabel').textContent='本地服务';const liveTarget=st.targetDate;liveExpected=liveTarget||'';
      let next=st.running?'正在核对行情，图表暂用上一份完整快照。':['partial','error'].includes(st.status)?(st.message||'更新未全部成功，已保留缓存。'):'';
      if(liveTarget && liveTarget>data.asOf && !st.running)next+=' 当前应有收盘 '+liveTarget+'，快照截至 '+data.asOf+'。';
      if(!version && st.version && (!st.dataThrough || st.dataThrough===data.asOf))version=st.version;
      if(st.version && st.version!==version){statusMsg=next;reloadData(st.version);}
      else if(statusKey!==next){statusMsg=next;groups();}
      statusKey=next;refreshTimer=setTimeout(()=>poll(),st.running?2000:30000);
    }catch(_){statusMsg='本地更新服务未连接；当前显示已保存的快照。';$('modeLabel').textContent='离线快照';$('refreshBtn').hidden=true;groups();refreshTimer=setTimeout(()=>poll(),30000);}
  }
  $('refreshBtn').onclick=()=>poll('POST');
  $('modeLabel').textContent=online?'本地服务':'离线快照';
  render();
  if(online)poll();
})();
