/* ===== 导入 ===== */
function impOnPick(input){
  const f = input.files[0];
  const btn = $("#impBtn"), name = $("#imp_name");
  if(f){ btn.classList.add("has-file"); name.textContent = f.name; }
  else { btn.classList.remove("has-file"); name.textContent = "未选择文件"; }
}
async function impParse(){
  const f = $("#imp_file").files[0]; if(!f) return msg("请选择文件", false);
  let useAi = $("#imp_ai").checked;
  if(useAi && !(await aiConfigured())){
    msg("未配置 AI：请到设置页填写 LLM API Key，本次按未启用 AI 解析", false);
    useAi = false;
  }
  const fd = new FormData(); fd.append("file", f);
  $("#imp_summary").textContent = "解析中…" + (useAi ? "（AI 兜底可能较慢）" : "");
  try{
    const r = await api(`/api/import/parse?ledger=${lid()}&use_ai=${useAi}`, {method:"POST", body:fd});
    impPreviews = r.previews;
    impMeta = {filename:r.filename, source:r.previews.find(p=>p.source)?.source||"manual"};
    await loadAccs();   // 解析期可能自动建了银行卡账户，同步账户列表供编辑弹窗/记一笔使用
    $("#imp_summary").innerHTML = `共 <b>${r.total}</b> 行：新 <b>${r.new}</b>，重复 ${r.duplicate}（将跳过），无效 ${r.skip}，待确认 <b style="color:var(--pending)">${r.need_confirm}</b>`;
    document.querySelector("#pv_summary").innerHTML = $("#imp_summary").innerHTML;
    renderPv();
    openPv();
  }catch(e){ $("#imp_summary").textContent=""; msg(e.message, false); }
}
function renderPv(){
  // _pvIdx 是显示序号（滤掉解析期 skip 行后连续），impSetCat/impSkipRow 按它直寻 impPreviews[i]
  let pvIdx = 0;
  document.querySelector("#pv_body").innerHTML = impPreviews
    .filter(p=>!p.skip || p._pvSkip)   // 解析期无效行不显示；用户跳过的行保留（半透明可恢复）
    .slice(0,500)
    .map(p => { p._pvIdx = pvIdx++; return txRow(p, "preview"); }).join("");
}
function impSkipRow(i){
  const p = impPreviews[i]; if(!p) return;
  p.skip = !p.skip;
  if(p.skip) p._pvSkip = true; else delete p._pvSkip;
  renderPv();
}

function openPv(){ $("#pvMask").style.display="flex"; }
function closePv(){ $("#pvMask").style.display="none"; }
async function impSetCat(sel){
  const i = +sel.dataset.pvIdx;
  const p = impPreviews[i];
  if(sel.value === "__new__"){
    const r = await newCategoryAndPick(p.type || "expense", "新分类名（自定义分类）：");
    if(!r){ sel.value = p.category_id || ""; return; }
    const opt = document.createElement("option");
    opt.value = r.id; opt.textContent = r.name;
    sel.insertBefore(opt, sel.querySelector('option[value="__new__"]'));
    sel.value = r.id;
  }
  p.user_changed = true;   // 仅用户手动改分类才回写学习映射
  p.category_id = +sel.value || null;
  p.need_confirm = false;
  p.classified_via = "manual";
  const cat = categories.find(c=>c.id === +sel.value);
  if(cat && cat.type === "transfer"){
    p.type = "transfer";
    p.tags = Object.assign({}, p.tags, {transfer_sub: cat.name === "退款" ? "退款" : "账户转账"});
    p.category_name = p.parent_category = p.tags.transfer_sub;
    renderPv();
    return;
  }
  // 移除该行的"待确认"标签
  const row = sel.closest(".tx-row");
  row && row.querySelector(".tag.warn")?.remove();
}
async function impConfirm(){
  if(!impPreviews) return;
  // 去掉仅前端用的标记（_pvIdx/_pvSkip），避免传给后端
  const payload = impPreviews.map(({_pvIdx, _pvSkip, ...rest}) => rest);
  const r = await apiSend(`/api/import/confirm?ledger=${lid()}`, "POST", {previews:payload, ...impMeta});
  msg(`已导入 ${r.imported} 笔（批次 #${r.batch_id}，可在下方撤销）`);
  closePv(); $("#imp_summary").textContent="";
  // 与记一笔/编辑同口径走 afterTxWrite：流水列表、待确认徽标/chip、汇总条、统计全部实时刷新

  afterTxWrite(); loadBatches(); loadAccs();
}
async function loadBatches(){
  const bs = await api(`/api/import/batches?ledger=${lid()}`);
  document.querySelector("#batchTable tbody").innerHTML = bs.map(b=>
    `<tr><td>${(b.imported_at||"").slice(0,16)}</td><td>${esc(b.filename)}</td><td>${esc(b.source)}</td><td>${b.count}</td>
     <td><button class="btn btn-ghost" style="padding:4px 10px;font-size:12px" onclick="undoBatch(${b.id})">撤销</button></td></tr>`).join("") ||
    '<tr><td colspan="5" class="sub">暂无批次</td></tr>';
}
async function undoBatch(id){
  if(!await uiConfirm("撤销将删除该批次全部流水，确定？", "撤销")) return;
  const r = await api(`/api/import/batches/${id}`, {method:"DELETE"});
  msg(`已撤销 ${r.deleted} 笔`); loadBatches(); afterTxWrite();
}