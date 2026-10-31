(async function(){
const $=id=>document.getElementById(id);
const bridge=window.AstrBotPluginView||window.AstrBotPluginPage;
let data,busy=false;
const esc=function(value){return String(value).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});};
const yuan=value=>esc(value)+' 元';
const labels={open:'投票中',planned:'已排期',done:'已完成',rejected:'不采用'};
function notify(message,error){const n=$('notice');n.textContent=message;n.classList.toggle('error',!!error);n.classList.remove('hidden');clearTimeout(notify.timer);notify.timer=setTimeout(()=>n.classList.add('hidden'),error?8000:4000);}
function empty(id,text){$(id).innerHTML='<div class="empty">'+esc(text)+'</div>';}
function row(amount,title,meta,actions){return '<div class="ledger-row"><div class="amount">'+amount+'</div><div class="row-main"><strong>'+title+'</strong><small>'+meta+'</small></div><div class="row-side">'+actions+'</div></div>';}
function render(){
 if(!data)return;
 $('loading').classList.add('hidden');$('app').classList.remove('hidden');
 $('serverTime').textContent=data.server_time;
 $('income').textContent=yuan(data.income);$('expense').textContent=yuan(data.expense);$('balance').textContent=yuan(data.balance);
 $('pending').textContent=data.pending_count+' 笔';$('pendingBrief').textContent=yuan(data.pending_income)+' 待核实';
 $('pendingTab').textContent=data.pending_count;$('activeTab').textContent=data.active_count;
 $('goalBrief').textContent=Number(data.goal)>0?'目标 '+yuan(data.goal):'未设运营目标';
 $('goalNumber').textContent=yuan(data.goal);$('goalInput').value=data.goal;$('goalFill').style.width=Math.min(100,data.progress)+'%';
 $('goalProgress').textContent=Number(data.goal)>0?'已确认收入覆盖 '+data.progress.toFixed(1)+'%':'设为 0 可隐藏目标';
 renderPending();renderRights();renderLedger();renderVotes();
}
function renderPending(){
 const rows=data.ledger.filter(r=>r.status==='pending');
 if(!rows.length)return empty('pendingList','没有待确认记录。登记时取消“已核对实际到账”，可先存后核。');
 $('pendingList').innerHTML=rows.map(r=>row('+'+yuan(r.amount),esc(r.note),esc(r.time_text)+' · '+esc(r.sponsor_key),'<button class="btn primary small" data-id="'+r.id+'">确认到账</button>')).join('');
 document.querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>{if(confirm('请先在支付平台核实到账。确认后开通 30 天权益并计入公示。'))perform({action:'confirm',id:b.dataset.id});});
}
function renderRights(){
 const q=$('rightsSearch').value.trim().toLowerCase();
 const rows=data.sponsors.filter(r=>!q||(r.target+r.display_name+r.tier_name).toLowerCase().includes(q));
 if(!rows.length)return empty('rightsList',q?'没有匹配的权益记录。':'还没有权益记录。');
 $('rightsList').innerHTML=rows.map(r=>row('<span class="badge '+(r.active?'active':'')+'">'+(r.active?'有效':'已到期')+'</span>',esc(r.display_name||r.target),esc(r.tier_name)+' · '+esc(r.target)+' · 至 '+esc(r.expires_text),'<button class="link-btn" data-renew="'+r.key+'">续期</button><button class="link-btn" data-revoke="'+r.key+'">撤销</button>')).join('');
 document.querySelectorAll('[data-renew]').forEach(b=>b.onclick=()=>showRenew(b.dataset.renew));
 document.querySelectorAll('[data-revoke]').forEach(b=>b.onclick=()=>{const r=data.sponsors.find(x=>x.key===b.dataset.revoke);if(confirm('撤销 '+(r.display_name||r.target)+' 的权益？历史账目保留，不自动退款。'))perform({action:'revoke',key:b.dataset.revoke});});
}
function showRenew(key){
 const box=$('renewBox');box.classList.remove('hidden');
 box.innerHTML='<input id="renewAmount" type="number" min="0" step="0.01" value="0"><input id="renewDays" type="number" min="1" max="3650" value="30"><button class="btn primary small" id="renewSubmit">确认续期</button><button class="link-btn" id="renewCancel">取消</button>';
 $('renewCancel').onclick=()=>box.classList.add('hidden');
 $('renewSubmit').onclick=()=>perform({action:'renew',key:key,amount:$('renewAmount').value,days:$('renewDays').value});
}
function renderLedger(){
 if(!data.ledger.length)return empty('ledgerList','暂无人工登记账目。');
 $('ledgerList').innerHTML=data.ledger.map(r=>row((r.kind==='income'?'+':'−')+yuan(r.amount),esc(r.note),esc(r.time_text)+' · '+esc(r.month),'<span class="badge '+(r.status==='pending'?'pending':'')+'">'+(r.status==='pending'?'待确认':'已确认')+'</span>')).join('');
}
function renderVotes(){
 if(!data.candidates.length)return empty('voteList','还没有功能提名。群成员可用 /功能投票 提名 功能名称 参与。');
 $('voteList').innerHTML=data.candidates.map(r=>row(r.votes+' 票',esc(r.title),esc(r.id)+' · '+esc(r.created_text),Object.entries(labels).filter(pair=>pair[0]!==r.status).map(pair=>'<button class="link-btn" data-vote="'+r.id+'" data-status="'+pair[0]+'">'+pair[1]+'</button>').join(''))).join('');
 document.querySelectorAll('[data-vote]').forEach(b=>b.onclick=()=>perform({action:'candidate',id:b.dataset.vote,status:b.dataset.status}));
}
async function perform(body){
 if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);
 try{const r=await bridge.apiPost('page/action',body);if(!r.success)throw Error(r.message||'操作失败');if(r.dashboard)data=r.dashboard;render();notify(r.message||'已保存');}
 catch(e){notify(e.message||'请求失败',true);}
 finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);}
}
$('registerForm').onsubmit=e=>{e.preventDefault();perform({action:'register',target:$('target').value,tier:$('tier').value,amount:$('amount').value,display_name:$('displayName').value,public:$('public').checked,confirmed:$('confirmed').checked});};
$('ledgerForm').onsubmit=e=>{e.preventDefault();perform({action:'ledger',kind:$('ledgerKind').value,amount:$('ledgerAmount').value,note:$('ledgerNote').value});};
$('goalForm').onsubmit=e=>{e.preventDefault();perform({action:'goal',goal:$('goalInput').value});};
$('rightsSearch').oninput=renderRights;
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===b));document.querySelectorAll('[id^="panel-"]').forEach(x=>x.classList.add('hidden'));$('panel-'+b.dataset.tab).classList.remove('hidden');$('renewBox').classList.add('hidden');});
try{if(bridge&&bridge.ready)await bridge.ready();data=await bridge.apiGet('page/dashboard');render();}catch(e){$('loading').textContent='无法打开台账：'+(e.message||'请重新登录控制台');}
})();
