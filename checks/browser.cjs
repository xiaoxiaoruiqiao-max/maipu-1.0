// Optional UI check: run with Playwright and Chromium installed (not needed by the dashboard).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname,'..'), out = path.join(root,'checks','evidence');

(async()=>{
  fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({headless:true,...(process.env.CHROME_EXE?{executablePath:process.env.CHROME_EXE}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1400,height:930},deviceScaleFactor:1});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:18916/',{waitUntil:'networkidle'});
    await page.locator('.chart svg').first().waitFor();
    const count=Number(await page.evaluate(()=>window.SECTOR_SNAPSHOT.count));
    assert.equal(await page.locator('.sector').count(),count);
    assert.equal(await page.locator('.sector').first().locator('h3').textContent(),'半导体');
    assert.equal(await page.locator('#refreshBtn').isVisible(),true);
    const report={count, timeframes:{}, errors};
    for(const [key,name] of [['D','日'],['W','周'],['M','月']]){
      await page.locator('[data-p="'+key+'"]').click();
      await page.locator('.chart svg').first().waitFor();
      const snapshot=await page.evaluate(p=>{
        const s=window.SECTOR_SNAPSHOT,axis=s.axes[p],last=axis.closed.lastIndexOf(true);
        return {cutoff:axis.dates[last], all:axis.dates.at(-1), scoreEligible:s.rows.filter(r=>r.series[p][last]?.[4]).length,
          distribution:[...document.querySelectorAll('.breadth-values b')].map(e=>+e.textContent),range:document.querySelector('#axisRange').textContent};
      },key);
      assert(snapshot.range.includes(snapshot.cutoff));
      assert.equal(snapshot.distribution.reduce((a,b)=>a+b),count);
      if(key!=='D' && snapshot.all!==snapshot.cutoff){
        await page.check('#partial');
        assert((await page.locator('#axisRange').textContent()).includes(snapshot.all));
        assert((await page.locator('#breadthPeriod').textContent()).includes(snapshot.cutoff));
        assert((await page.locator('#notice').textContent()).includes('未收盘期'));
        await page.uncheck('#partial');
      }
      report.timeframes[key]=snapshot;
    }
    await page.locator('[data-p="D"]').click();await page.locator('.chart svg').first().waitFor();
    await page.selectOption('#length','30');assert((await page.locator('#axisRange').textContent()).includes('30 期'));
    await page.selectOption('#scale','percent');await page.waitForFunction(()=>document.querySelector('.chart svg')?.textContent.includes('%'));
    await page.locator('.chart').first().click({position:{x:180,y:45}});
    assert((await page.locator('#pinned').textContent()).includes('已锁定'));
    const xs=await page.locator('.cross').evaluateAll(es=>es.slice(0,3).map(e=>e.getBoundingClientRect().x));
    assert(Math.max(...xs)-Math.min(...xs)<1);
    await page.mouse.wheel(0,1500);await page.waitForFunction(()=>window.scrollY>700);
    assert((await page.locator('#pinned').textContent()).includes('已锁定'));
    await page.locator('.sector').last().scrollIntoViewIfNeeded();await page.locator('.sector').last().locator('svg').waitFor();
    report.lastRow=await page.locator('.sector').last().locator('h3').textContent();
    await page.keyboard.press('Escape');await page.evaluate(()=>window.scrollTo(0,0));
    await page.selectOption('#sort','potential');
    const scores=await page.locator('.scores .score-row:not(.risk) b').allTextContents();
    assert.equal(scores.length,count);
    assert.deepEqual(scores.map(n=>n==='—'?-1:+n),scores.map(n=>n==='—'?-1:+n).sort((a,b)=>b-a));
    await page.fill('#search','881121');assert.equal(await page.locator('.sector').count(),1);
    await page.getByRole('button',{name:'查看评分依据'}).click();
    assert((await page.locator('#dialogTitle').innerText()).includes('半导体'));
    assert.equal(await page.locator('.score-detail .total').count(),2);
    assert.equal(await page.locator('.horizons tbody tr').count(),3);
    await page.keyboard.press('Escape');await page.fill('#search','没有这个行业');
    assert.equal(await page.locator('.sector').count(),0);assert(await page.locator('.empty').isVisible());
    await page.fill('#search','');await page.selectOption('#length','60');await page.selectOption('#sort','catalog');
    const pastDate=await page.evaluate(()=>window.SECTOR_SNAPSHOT.axes.D.dates.at(-75));
    await page.fill('#endDate',pastDate);assert((await page.locator('#breadthPeriod').textContent()).includes(pastDate));
    await page.locator('#latestBtn').click();await page.selectOption('#scale','points');
    await page.getByRole('button',{name:/读图与评分/}).click();assert(await page.locator('#dialog').isVisible());await page.keyboard.press('Escape');
    await page.locator('#methodBtn').evaluate(b=>b.blur());await page.mouse.move(1,1);await page.locator('.chart svg').first().waitFor();
    await page.screenshot({path:path.join(out,'desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.querySelector('.chart svg')?.viewBox.baseVal.width===document.querySelector('.chart').clientWidth);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(out,'mobile.png')});
    const off=await browser.newPage();off.on('pageerror',e=>errors.push(e.message));
    await off.goto(pathToFileURL(path.join(root,'dist','index.html')).href);
    await off.locator('.chart svg').first().waitFor();assert.equal(await off.locator('.sector').count(),count);
    assert.equal(await off.locator('#modeLabel').textContent(),'离线快照');
    assert.equal(await off.locator('#refreshBtn').isVisible(),false);
    report.offline=true;assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
