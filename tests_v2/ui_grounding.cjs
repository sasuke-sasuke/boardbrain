/* Structured answers and inspected-image controls; synthetic fixtures, no model calls. */
const fs=require('fs'),path=require('path'),assert=require('assert');
const {execFileSync}=require('child_process');
const {chromium}=require(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES?process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES+'/playwright':'playwright');
(async()=>{
  const runtime=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const base=`http://127.0.0.1:${runtime.port}`,headers={Authorization:`Bearer ${runtime.token}`,'Content-Type':'application/json'};
  const api=async(route,data)=>{const r=await fetch(base+route,{headers,method:data===undefined?'GET':'POST',body:data===undefined?undefined:JSON.stringify(data)});const result=await r.json();assert(r.ok,JSON.stringify(result));return result;};
  const fixtures=JSON.parse(execFileSync(process.env.BOARDBRAIN_TEST_PYTHON||'python3',['-c',[
    'import pymupdf as fitz','import io,json,base64','from PIL import Image,ImageDraw','doc=fitz.open()',
    'for n in range(2):',' p=doc.new_page(width=1200,height=800)',' p.insert_text((70,70),f"Synthetic schematic page {n+1}",fontsize=22)',
    'im=Image.new("RGB",(1800,900),"white");draw=ImageDraw.Draw(im)','draw.rectangle((100,150,1400,700),outline="black",width=6)',
    'draw.text((150,200),"U1 pin A1 / TEST_GATE / synthetic inspected image",fill="black")','out=io.BytesIO();im.save(out,"PNG")',
    'print(json.dumps({"pdf":base64.b64encode(doc.tobytes()).decode(),"image":base64.b64encode(out.getvalue()).decode()}))'
  ].join('\n')],{encoding:'utf8'}));
  const ref=await api('/api/cases',{kind:'reference',title:'Grounded answer regression',board_id:'TEST-002'});
  await api('/api/upload',{case_id:ref.id,filename:'synthetic-grounding.pdf',role:'schematic',content:fixtures.pdf});
  let saved;const deadline=Date.now()+15000;
  while(true){saved=await api('/api/case?id='+ref.id);if(saved.sources.length===1&&saved.sources[0].status==='ready')break;assert(Date.now()<deadline,'Fixture import timed out');await new Promise(r=>setTimeout(r,100));}
  const source=saved.sources[0];source.info.sheet_index={'2':{sheet:'12',page_title:'Synthetic power'}};
  const pinId='pin:opaque-fixture',docId='doc:opaque-fixture',imageId='image:opaque-fixture',otherId='doc:unused-fixture',fullImageId='image:unused-full-fixture';
  const evidence={
    [pinId]:{id:pinId,kind:'pin',source_id:'synthetic-boardview',name:'synthetic.bvr',scope_confirmed:false,data:{ref:'U1',pin:'1',name:'A1',net:'TEST_GATE',side:'T'}},
    [docId]:{id:docId,kind:'document',source_id:source.id,name:source.name,scope_confirmed:false,data:{page:2,sheet:'12',page_title:'Synthetic power',text:'Example source excerpt'}},
    [imageId]:{id:imageId,kind:'document_image',source_id:source.id,name:source.name,scope_confirmed:false,data:{page:2,sheet:'12',page_title:'Synthetic power',region:[.1,.2,.7,.8],focus:'U1',source_sha:'synthetic',render_version:'synthetic',pixel_width:1800,pixel_height:900,image_sha256:'synthetic'}},
    [otherId]:{id:otherId,kind:'document',source_id:source.id,name:source.name,scope_confirmed:false,data:{page:1,text:'Retrieved but unused'}},
    [fullImageId]:{id:fullImageId,kind:'document_image',source_id:source.id,name:source.name,scope_confirmed:false,data:{page:1,region:[0,0,1,1]}}
  };
  const claim=(kind,text,ids=[],extra={})=>({kind,text,evidence_ids:ids,quote:'',ref:'',pin:'',net:'',...extra});
  const plan={summary:'Legacy summary must not appear with reference claims.',tests:[],questions:[],hypotheses:[],web_sources:[],claims:[
    claim('pin_connection','Do not show this free-form pin assertion.',[pinId],{ref:'U1',pin:'1',net:'TEST_GATE'}),
    claim('document_fact','The document includes this source text.',[docId],{quote:'Example source excerpt'}),
    claim('visual_observation','A labeled connection is visible in the enlarged section.',[imageId]),
    claim('inference','Circuit behavior remains an interpretation.',[docId,imageId]),
    claim('general','A net name identifies an electrical connection.'),
    claim('unknown','An expected operating voltage has not been established.')
  ]};
  let mode='grounded',failImage=false,delayImage=0,inspectionRequests=0;
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1320,height:900},deviceScaleFactor:1});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/case?**',async route=>{
      if(new URL(route.request().url()).searchParams.get('id')!==ref.id)return route.continue();
      const body=structuredClone(saved),answer=structuredClone(plan);
      if(mode==='legacy')delete answer.claims;
      if(mode==='repair')body.kind='repair';
      if(mode==='mismatch')answer.claims=[claim('pin_connection','Wrong net must not display.',[pinId],{ref:'U1',pin:'1',net:'WRONG_NET'})];
      body.events.push({id:'synthetic-plan',kind:'plan',created:saved.updated,data:{plan:answer,evidence,model:'gpt-6.1-sol',estimated_cost:0,elapsed_seconds:0}});
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
    });
    await page.route('**/api/inspection?**',async route=>{
      inspectionRequests++;assert.equal(route.request().headers().authorization,headers.Authorization);
      const url=new URL(route.request().url());assert.equal(url.searchParams.get('case_id'),ref.id);assert([imageId,fullImageId].includes(url.searchParams.get('id')));
      if(delayImage)await new Promise(r=>setTimeout(r,delayImage));
      await route.fulfill(failImage?{status:400,contentType:'application/json',body:JSON.stringify({error:'Synthetic inspection integrity failure'})}:{status:200,contentType:'image/png',body:Buffer.from(fixtures.image,'base64')}).catch(()=>{});
    });
    await page.goto(base+'/#'+runtime.token);await page.locator(`[data-case="${ref.id}"]`).click();
    const answer=page.locator('.grounded-answer'),dialog=page.getByRole('dialog');
    await answer.locator('.answer-claim').nth(5).waitFor();assert.equal(await answer.locator('.answer-claim').count(),6);
    assert((await answer.locator('.claim-connection').textContent()).includes('U1.A1'));
    assert((await answer.locator('.claim-net').textContent()).includes('TEST_GATE'));
    const answerText=await page.locator('#plan').textContent();
    for(const text of ['Do not show this free-form','Legacy summary must not appear','opaque-fixture'])assert(!answerText.includes(text));
    assert.equal(await answer.locator('blockquote').textContent(),'Example source excerpt');
    await page.locator('.plan-sources h3').getByText('Sources used',{exact:true}).waitFor();
    assert.equal(await page.locator('.other-evidence').getAttribute('open'),null);
    assert.equal(await page.locator('.other-evidence button').first().isVisible(),false);
    const imageChip=answer.locator('.claim-visual_observation button');
    assert.equal(await imageChip.textContent(),'PDF page 2 · enlarged section · schematic sheet 12');
    await imageChip.click();
    const loaded=()=>page.waitForFunction(()=>document.querySelector('#inspection-image')?.naturalWidth===1800&&!document.querySelector('#inspection-image').hidden);
    await loaded();assert((await dialog.boundingBox()).width>1180);
    await dialog.getByText('Image sent to the assistant; interpretation still needs checking.',{exact:true}).waitFor();
    assert((await dialog.locator('.inspection-context').textContent()).includes('10–70% across, 20–80% down'));
    await dialog.getByRole('button',{name:'Actual pixels',exact:true}).click();
    assert.equal(Math.round((await dialog.locator('#inspection-image').boundingBox()).width),1800);
    await dialog.getByRole('button',{name:'Fit width',exact:true}).click();
    assert((await dialog.locator('#inspection-image').boundingBox()).width<(await dialog.locator('#inspection-viewport').boundingBox()).width);
    if(process.argv[3])await page.screenshot({path:path.join(process.argv[3],'inspected-image.png')});
    await dialog.getByRole('button',{name:'Open full page',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#pdf-status')?.textContent.includes('PDF page 2 loaded'));
    assert.equal(await page.getByLabel('PDF page number',{exact:true}).inputValue(),'2');
    await page.getByRole('button',{name:'Close dialog',exact:true}).click();
    failImage=true;await imageChip.click();await page.locator('#modal-error').getByText('Synthetic inspection integrity failure',{exact:true}).waitFor();
    failImage=false;await page.getByRole('button',{name:'Retry image',exact:true}).click();await loaded();
    assert.equal(await page.locator('#modal-error').isVisible(),false);
    await page.getByRole('button',{name:'Close dialog',exact:true}).click();
    delayImage=350;await imageChip.click();await page.getByRole('button',{name:'Close dialog',exact:true}).click();
    await page.getByRole('button',{name:'Settings & equipment',exact:true}).click();await page.waitForTimeout(500);
    assert.equal(await page.locator('#inspection-image').count(),0);assert.equal(await dialog.locator('#settings-form').count(),1);
    await page.getByRole('button',{name:'Close dialog',exact:true}).click();delayImage=0;
    await page.locator('.other-evidence summary').click();
    const whole=page.locator('.other-evidence').getByRole('button',{name:'PDF page 1 · image',exact:true});await whole.click();await loaded();
    await dialog.getByText('Full schematic page.',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Close dialog',exact:true}).click();
    if(process.argv[3])await page.screenshot({path:path.join(process.argv[3],'grounded-answer.png')});
    mode='repair';await page.reload();await answer.locator('.answer-claim').nth(5).waitFor();
    assert.equal(await page.locator('#plan-label').textContent(),'NEXT USEFUL TEST');
    mode='legacy';await page.reload();await page.locator('#plan .summary').getByText(plan.summary,{exact:true}).waitFor();
    assert.equal(await answer.count(),0);await page.locator('#plan h3').getByText('Evidence retrieved',{exact:true}).waitFor();
    mode='mismatch';await page.reload();await answer.getByText('Pin connection not established by the attached sources.',{exact:true}).waitFor();
    assert(!(await answer.textContent()).includes('WRONG_NET'));
    assert(inspectionRequests>=5);assert.deepEqual(errors,[]);
    console.log('Grounding UI passed: typed claims, source-derived pin labels, citations, retrieved/source distinction, inspection images, full-page navigation, authenticated requests, retry, cancellation, legacy plans and repair rendering.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
