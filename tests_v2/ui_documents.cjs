/* Real PDF/photo requests and browser controls; synthetic files only, no model calls. */
const fs=require('fs'), path=require('path'), assert=require('assert');
const {execFileSync}=require('child_process');
const {chromium}=require(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES?process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES+'/playwright':'playwright');
(async()=>{
  const runtime=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const base=`http://127.0.0.1:${runtime.port}`, headers={Authorization:`Bearer ${runtime.token}`,'Content-Type':'application/json'};
  const api=async(route,data)=>{const r=await fetch(base+route,{headers,method:data===undefined?'GET':'POST',body:data===undefined?undefined:JSON.stringify(data)});const result=await r.json();assert(r.ok,JSON.stringify(result));return result;};
  const fixtures=JSON.parse(execFileSync(process.env.BOARDBRAIN_TEST_PYTHON||'python3',['-c',[
    'import pymupdf as fitz', 'import io,json,base64', 'from PIL import Image', 'doc=fitz.open()',
    'for n in range(2):', ' p=doc.new_page(width=1200,height=800)',
    ' p.insert_text((70,70),f"U1 synthetic schematic page {n+1}",fontsize=22)',
    ' p.draw_rect(fitz.Rect(100,160,520,500))',
    ' p.insert_text((120,200),"A1   USB_D+   Synthetic connection",fontsize=14)',
    'photo=io.BytesIO();Image.new("RGB",(100,80),"green").save(photo,"PNG")',
    'print(json.dumps({"pdf":base64.b64encode(doc.tobytes()).decode(),"photo":base64.b64encode(photo.getvalue()).decode()}))'
  ].join('\n')],{encoding:'utf8'}));
  const ref=await api('/api/cases',{kind:'reference',title:'Viewer regression reference',board_id:'TEST-001'});
  for(const [filename,role,content] of [
    ['synthetic.bvr','boardview',Buffer.from('BVRAW_FORMAT_3\nPART_NAME U1\nPART_SIDE T\nPART_ORIGIN 10 20\nPIN_NUMBER 1\nPIN_NAME A1\nPIN_ORIGIN 2 3\nPIN_NET USB_D+\nPIN_END\nPART_END\n').toString('base64')],
    ['synthetic.pdf','schematic',fixtures.pdf],['synthetic.png','photo',fixtures.photo]]) {
    await api('/api/upload',{case_id:ref.id,filename,role,content});
  }
  const deadline=Date.now()+15000;
  while(true){const c=await api('/api/case?id='+ref.id);if(c.sources.length===3&&c.sources.every(s=>['ready','parsed'].includes(s.status)))break;assert(Date.now()<deadline,'Fixture import timed out');await new Promise(r=>setTimeout(r,100));}
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1320,height:900},deviceScaleFactor:1});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/#'+runtime.token);
    await page.locator(`[data-case="${ref.id}"]`).click();
    await page.locator('#lookup-query').fill('U1.A1');await page.locator('#lookup-form button').click();
    const results=page.locator('#lookup-results'), dialog=page.getByRole('dialog');
    await results.getByText('U1.A1 → USB_D+',{exact:true}).waitFor();
    assert.equal(await results.locator('h4').first().textContent(),'Pin connections');
    await results.getByRole('button',{name:'View pin details'}).click();
    await dialog.locator('.pin-connection strong').getByText('USB_D+',{exact:true}).waitFor();
    assert((await dialog.locator('.evidence-facts').textContent()).includes('numeric identifier'));
    assert.equal(await dialog.locator('pre').isVisible(),false);
    if(process.argv[3])await page.screenshot({path:path.join(process.argv[3],'pin-details.png')});
    await page.getByRole('button',{name:'Close dialog'}).click();
    await results.getByRole('button',{name:'Open schematic page'}).first().click();
    const loaded=async(n)=>page.waitForFunction(n=>document.querySelector('#pdf-status')?.textContent.includes(`PDF page ${n} loaded`)&&document.querySelector('#pdf-page-image')?.naturalWidth>0,n);
    await loaded(1);
    assert((await dialog.boundingBox()).width>1180,'Viewer should fill most of the app window');
    await page.getByLabel('Schematic zoom',{exact:true}).selectOption('3');
    await page.waitForFunction(()=>{const i=document.querySelector('#pdf-page-image'),v=document.querySelector('#pdf-viewport');return i.naturalWidth>3000&&i.getBoundingClientRect().width>v.clientWidth*2.5;});
    await page.locator('#pdf-viewport').evaluate(v=>{v.scrollLeft=300;v.scrollTop=150;});
    assert((await page.locator('#pdf-viewport').evaluate(v=>v.scrollLeft))>0);
    await page.getByRole('button',{name:'Next page',exact:true}).click();await loaded(2);
    assert(await page.getByRole('button',{name:'Next page',exact:true}).isDisabled());
    await page.getByRole('button',{name:'Previous page',exact:true}).click();await loaded(1);
    await page.getByLabel('PDF page number',{exact:true}).fill('2');
    await page.getByRole('button',{name:'Go to page',exact:true}).click();await loaded(2);
    await page.getByRole('button',{name:'Fit width',exact:true}).click();
    assert.equal(await page.getByLabel('Schematic zoom',{exact:true}).inputValue(),'1');
    const widths=await page.evaluate(()=>[document.querySelector('#pdf-page-image').getBoundingClientRect().width,document.querySelector('#pdf-viewport').clientWidth]);
    assert(widths[0]<=widths[1]&&widths[0]>widths[1]*.9);
    await page.getByLabel('PDF page number',{exact:true}).fill('999');
    assert.equal(await page.getByLabel('PDF page number',{exact:true}).evaluate(e=>e.checkValidity()),false);
    await page.getByLabel('PDF page number',{exact:true}).fill('2');
    await page.route('**/api/page?**',route=>route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Synthetic page load failure'})}));
    await page.getByRole('button',{name:'Go to page',exact:true}).click();
    await page.locator('#modal-error').getByText('Synthetic page load failure',{exact:true}).waitFor();
    await page.unroute('**/api/page?**');
    await page.getByRole('button',{name:'Go to page',exact:true}).click();await loaded(2);
    assert.equal(await page.locator('#modal-error').isVisible(),false);
    if(process.argv[3])await page.screenshot({path:path.join(process.argv[3],'schematic-viewer.png')});
    await page.setViewportSize({width:850,height:620});
    assert((await page.locator('#pdf-viewport').boundingBox()).height>100);
    await page.route('**/api/page?**',async route=>{await new Promise(r=>setTimeout(r,300));await route.continue().catch(()=>{});});
    await page.getByRole('button',{name:'Go to page',exact:true}).click();
    await page.getByRole('button',{name:'Close dialog'}).click();
    await page.getByRole('button',{name:'Settings & equipment',exact:true}).click();
    await page.waitForTimeout(450);
    assert.equal(await page.locator('#pdf-page-image').count(),0);
    assert.equal(await dialog.locator('#settings-form').count(),1);
    await page.unroute('**/api/page?**');
    await page.getByRole('button',{name:'Close dialog'}).click();
    await page.getByRole('tab',{name:'Files & photos',exact:false}).click();
    await page.getByRole('button',{name:'View photo',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.photo-preview')?.naturalWidth>0);
    assert.deepEqual(errors,[]);
    console.log('Document UI passed: pin details, wide viewer, zoom rendering, pan, page navigation, bounds, error/retry, resize, cancellation and photo preview.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
