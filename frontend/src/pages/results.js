import {jobs} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {chart} from '../components/common/chart.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
const REASON_TEXT={
 no_candidate:'窗口内没有严格晚于该事件的后继事件',
 beyond_window:'最近的后继事件超出跟随窗口，时滞不满足要求',
 not_earlier:'窗口内没有严格早于该后继的源事件，无法形成有效时滞',
 exclusive_taken:'窗口内后继已被其他源事件配对（一对一口径下后继不可复用）',
 no_prior_source:'没有严格早于该事件的源事件',
 exclusive_source_taken:'窗口内源事件已与其他后继配对（一对一口径下源事件不可复用）'
};
function reasonCell(code,detail){return esc(REASON_TEXT[code]||detail||code);}
function pairReviewPanel(r){
 const review=r.pair_review;
 if(!Array.isArray(review)||!review.length)return '';
 const first=review[0];
 const options=review.map(item=>'<option value="'+esc(item.link_id)+'">'+esc(item.link_id+' · '+item.label+' · 配对'+item.matched_pairs.length+'对')+'</option>').join('');
 const blocks=review.map(item=>{
  const pairs=item.matched_pairs.length?'<div class="table-wrap"><table><thead><tr><th>源事件A</th><th>A发生(累计分钟)</th><th>后继事件B</th><th>B发生(累计分钟)</th><th>时滞(min)</th></tr></thead><tbody>'+item.matched_pairs.map(p=>'<tr><td>'+esc(p.source_event_id)+'</td><td>'+esc(fmt(p.source_minute))+'</td><td>'+esc(p.target_event_id)+'</td><td>'+esc(fmt(p.target_minute))+'</td><td>'+esc(fmt(p.lag_min))+'</td></tr>').join('')+'</tbody></table></div>':'<p class="empty">该服务对没有有效配对。</p>';
  const missSrc=item.unmatched_sources.length?'<div class="table-wrap"><table><thead><tr><th>未匹配源事件A</th><th>发生(累计分钟)</th><th>未匹配原因</th></tr></thead><tbody>'+item.unmatched_sources.map(p=>'<tr><td>'+esc(p.event_id)+'</td><td>'+esc(fmt(p.minute))+'</td><td>'+reasonCell(p.reason,p.reason_detail)+'</td></tr>').join('')+'</tbody></table></div>':'<p class="empty">源事件全部配对成功。</p>';
  const missTgt=item.unmatched_targets.length?'<div class="table-wrap"><table><thead><tr><th>未匹配后继事件B</th><th>发生(累计分钟)</th><th>未匹配原因</th></tr></thead><tbody>'+item.unmatched_targets.map(p=>'<tr><td>'+esc(p.event_id)+'</td><td>'+esc(fmt(p.minute))+'</td><td>'+reasonCell(p.reason,p.reason_detail)+'</td></tr>').join('')+'</tbody></table></div>':'<p class="empty">后继事件全部被配对。</p>';
  return '<div class="pair-review-block" data-pair="'+esc(item.link_id)+'"'+(item.link_id===first.link_id?'':' hidden')+'><div class="pair-review-grid"><section><div class="section-title"><h2>已配对事件（每个事件最多一次）</h2><span>'+item.matched_pairs.length+' 对</span></div>'+pairs+'</section><section><div class="section-title"><h2>未匹配源事件与原因</h2><span>'+item.unmatched_sources.length+' 条</span></div>'+missSrc+'</section><section><div class="section-title"><h2>未匹配后继事件与原因</h2><span>'+item.unmatched_targets.length+' 条</span></div>'+missTgt+'</section></div></div>';
 }).join('');
 return '<section class="panel pair-review"><div class="section-title"><h2>一对一配对回查（不复用后继）</h2><span>先配对最多，再总时滞最小 · 严格晚于且不超时窗</span></div><label class="pair-select">选择服务对<select id="pair-review-select">'+options+'</select></label>'+blocks+'</section>';
}
export async function results(id){
 workspace.jobs=await jobs.list();const j=id?await jobs.get(id):(workspace.jobs.find(j=>j.id===workspace.selectedJob)||workspace.jobs[0]);
 if(j&&!workspace.jobs.some(x=>x.id===j.id))workspace.jobs.push(j);
 if(!j)return heading('04 / REVIEW','结果复核','尚无分析结果。')+'<div class="empty">先到分析工作台，选择批次并提交一次计算。<a href="#/workbench">前往工作台 ↗</a></div>';
 workspace.selectedJob=j.id;const r=j.result||{};
 return heading('04 / REVIEW','结果复核与业务解释',j.name,'<button class="button" id="refresh-results">刷新结果 ↻</button>')+'<div class="result-selector"><label>分析记录<select id="select-job">'+workspace.jobs.map(x=>'<option value="'+x.id+'" '+(x.id===j.id?'selected':'')+'>'+esc(x.name)+' · '+esc(x.status)+'</option>').join('')+'</select></label>'+status(j.status)+'<span>'+date(j.created_at)+'</span><span class="approval">'+(j.approved?'已获业务批准':'待负责人复核')+'</span></div>'+(j.status==='completed'?'<section class="result-lead"><p class="eyebrow">COMPUTED FINDING</p><h2>'+esc(r.summary)+'</h2><div class="metrics">'+(r.metrics||[]).map(m=>'<div><span>'+esc(m.label)+'</span><strong>'+esc(fmt(m.value))+'<small>'+esc(m.unit||'')+'</small></strong></div>').join('')+'</div></section><section class="panel">'+chart(r.chart)+'</section><div class="two-columns"><section class="panel"><div class="section-title"><h2>业务解释</h2><span>由计算结果生成</span></div><ol class="questions">'+(r.findings||[]).map(v=>'<li>'+esc(v)+'</li>').join('')+'</ol>'+(r.warnings||[]).map(v=>'<p class="warning">'+esc(v)+'</p>').join('')+'</section><section class="method-sheet compact"><p class="eyebrow">REPRODUCIBILITY</p><h3>计算口径与参数</h3><p>'+esc(r.method)+'</p><pre>'+esc(JSON.stringify(j.params,null,2))+'</pre>'+(session.user.role==='admin'&&!j.approved?'<button class="primary" id="approve-job">批准此业务结论</button>':'')+'<p class="hint">仅批准结果可进入定时分发档案。</p></section></div><section class="panel"><div class="section-title"><h2>结果明细</h2><span>'+(r.table?.rows?.length||0)+' 行</span></div>'+table(r.table?.columns||[],r.table?.rows||[])+'</section>'+pairReviewPanel(r):'<section class="panel empty">'+(j.status==='failed'?'计算失败：'+esc(j.error)+'。请返回工作台调整参数或数据。':'后台正在执行计算。点击刷新结果查看最新进展。')+'<a href="#/workbench">返回工作台 ↗</a></section>');
}
export function bindResults(refresh){document.querySelector('#refresh-results')?.addEventListener('click',refresh);document.querySelector('#select-job')?.addEventListener('change',e=>{workspace.selectedJob=Number(e.target.value);location.hash='#/results/'+e.target.value;});document.querySelector('#approve-job')?.addEventListener('click',async()=>{try{await jobs.approve(workspace.selectedJob);notify('已批准，可创建分发计划');await refresh();}catch(e){notify(e.message,true);}});document.querySelector('#pair-review-select')?.addEventListener('change',e=>{const wanted=e.target.value;document.querySelectorAll('.pair-review-block').forEach(block=>{block.hidden=block.dataset.pair!==wanted;});});}
