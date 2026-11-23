'use strict';
const sheet={pending:false};const message=document.getElementById('message');
async function recount(input){if(sheet.pending)return;sheet.pending=true;message.textContent='工时重算中…';try{const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)});const data=await response.json();if(!response.ok)throw new Error(data.message);location.reload();}catch(error){message.textContent=error.message+'，请修正打卡';}finally{sheet.pending=false;}}
document.getElementById('recount').addEventListener('click',()=>recount({sample:'dirty.csv'}));
const file=document.getElementById('clock-file');if(file)file.addEventListener('submit',event=>{event.preventDefault();const values=new FormData(file);const csv=values.get('csv');recount(csv?{csv}:{sample:values.get('sample')});});
const check=document.getElementById('check');if(check)check.addEventListener('click',async()=>{try{const response=await fetch('/api/runs/'+check.dataset.id+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:Number(check.dataset.version),decision:'通过'})});const data=await response.json();if(!response.ok)throw Error(data.message);check.dataset.version=data.review_version;message.textContent='工资复核通过 / '+data.review_version;}catch(error){message.textContent=error.message;}});
const paper=document.getElementById('paper');if(paper)paper.addEventListener('click',()=>window.print());
const issue=document.getElementById('issue-advance');
if(issue)issue.addEventListener('click',async()=>{
  const out=document.getElementById('advance-result');
  try{
    const response=await fetch('/api/advances',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sample:new URLSearchParams(location.search).get('sample')||'dirty.csv',parameters:window.__ledgerParams||{}})});
    const data=await response.json();if(!response.ok)throw Error(data.message);
    out.textContent=(data.created?'已生成':'重跑复用')+' / '+data.advance_id+' / '+data.status+' / '+data.summary.amount+' CNY';
    location.reload();
  }catch(error){out.textContent=error.message;}
});
const settle=document.getElementById('run-settle');
if(settle)settle.addEventListener('click',async()=>{
  const out=document.getElementById('settle-result');
  try{
    const response=await fetch('/api/settlements',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sample:new URLSearchParams(location.search).get('sample')||'dirty.csv',parameters:window.__ledgerParams||{}})});
    const data=await response.json();if(!response.ok)throw Error(data.message);
    out.textContent=(data.created?'已生成 v'+data.version:'重跑复用 v'+data.version)+' / 对平='+data.balanced;
    location.reload();
  }catch(error){out.textContent=error.message;}
});
const seal=document.getElementById('seal-settle');
if(seal)seal.addEventListener('click',async()=>{
  const out=document.getElementById('settle-result');
  try{
    const current=(await (await fetch('/api/settlements')).json())[0];
    const response=await fetch('/api/settlements/'+current.settlement_id+'/seal',{method:'POST'});
    const data=await response.json();if(!response.ok)throw Error(data.message);
    out.textContent='已封存 / '+data.settlement_id;location.reload();
  }catch(error){out.textContent=error.message;}
});
const exportLink=document.getElementById('settle-export');
if(exportLink)exportLink.addEventListener('click',async event=>{
  event.preventDefault();
  try{
    const current=(await (await fetch('/api/settlements')).json())[0];
    const response=await fetch('/api/settlements/'+current.settlement_id+'/export.csv');
    if(!response.ok){const d=await response.json();throw Error(d.message);}
    const blob=await response.blob();const url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download='settlement.csv';a.click();URL.revokeObjectURL(url);
  }catch(error){document.getElementById('settle-result').textContent=error.message;}
});
document.querySelectorAll('td[tabindex]').forEach(cell=>cell.addEventListener('focus',()=>message.textContent=cell.textContent.trim()));
