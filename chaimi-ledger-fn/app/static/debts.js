/* ===== 借出借入 · 独立台账（不计收支；API 见 server.py 债务区） ===== */
let debtEditId = null;    // null=新增；数字=编辑
let repayDebtId = null;   // 还款弹窗目标债务
let repaysDebt = null;    // 明细弹窗目标债务
const DIR_CN = {out: "借出", in: "借入"};

async function loadDebts(){
  const status = $("#debtStatus") ? $("#debtStatus").value : "open";
  let sum = null, rows = [];
  try{
    [sum, rows] = await Promise.all([
      api(`/api/debts/summary?ledger=${lid()}`),
      api(`/api/debts?ledger=${lid()}&status=${status}`),
    ]);
  }catch(e){ msg("债务加载失败：" + e.message, false); return; }
  // 汇总卡：总借出在外 / 总借入在外 / 逾期
  const ov = sum.overdue.length;
  $("#debtCards").innerHTML = `
    <div class="card" style="flex:1;min-width:150px;margin:0;padding:12px 16px">
      <div class="sub">总借出在外</div><b class="num" style="font-size:20px">${curSym()}${fmt(sum.out.total)}</b>
      <span class="sub" style="margin-left:6px">${sum.out.count} 笔</span></div>
    <div class="card" style="flex:1;min-width:150px;margin:0;padding:12px 16px">
      <div class="sub">总借入在外</div><b class="num" style="font-size:20px">${curSym()}${fmt(sum.in.total)}</b>
      <span class="sub" style="margin-left:6px">${sum.in.count} 笔</span></div>
    <div class="card" style="flex:1;min-width:150px;margin:0;padding:12px 16px">
      <div class="sub">逾期未还</div><b class="num" style="font-size:20px;${ov?"color:var(--m-coral,#e8654a)":""}">${ov}</b>
      <span class="sub" style="margin-left:6px">笔</span></div>`;
  $("#debtTable tbody").innerHTML = rows.map(d => {
    const remaining = d.amount - d.repaid;
    const overdue = d.status === "open" && d.due_date && d.due_date < today();
    return `<tr>
      <td>${esc(d.person)}</td>
      <td>${DIR_CN[d.direction] || d.direction}</td>
      <td style="text-align:right" class="num">${fmt(d.amount)}</td>
      <td style="text-align:right" class="num">${fmt(d.repaid)}</td>
      <td style="text-align:right" class="num"><b>${fmt(remaining)}</b></td>
      <td>${esc(d.date)}</td>
      <td>${d.due_date ? esc(d.due_date) + (overdue ? ' <span class="bdg" style="color:var(--m-coral,#e8654a)">逾期</span>' : "") : '<span class="sub">—</span>'}</td>
      <td>${d.status === "settled" ? '<span class="sub">已还清</span>' : '<b>未还清</b>'}</td>
      <td style="white-space:nowrap;text-align:right">
        <button class="btn btn-ghost" style="padding:2px 8px;font-size:12px" onclick="repaysOpen(${d.id}, ${qEsc(d.person)}, '${d.direction}')">明细</button>
        ${d.status === "open" ? `<button class="btn btn-ghost" style="padding:2px 8px;font-size:12px" onclick="repayOpen(${d.id}, ${qEsc(d.person)}, '${d.direction}', ${remaining})">还款</button>` : ""}
        <button class="btn btn-ghost" style="padding:2px 8px;font-size:12px" onclick="debtEdit(${d.id})">编辑</button>
        <button class="btn btn-ghost" style="padding:2px 8px;font-size:12px" onclick="debtDel(${d.id}, ${qEsc(d.person)})">删</button>
      </td></tr>`;
  }).join("");
  $("#debtEmpty").style.display = rows.length ? "none" : "block";
}

function debtOpen(dir){
  debtEditId = null;
  $("#debtTitle").textContent = dir === "in" ? "新增借入" : "新增借出";
  $("#db_dir").value = dir;
  $("#db_person").value = ""; $("#db_amt").value = "";
  $("#db_date").value = today(); $("#db_due").value = ""; $("#db_note").value = "";
  $("#debtMask").style.display = "flex";
}
async function debtEdit(id){
  const rows = await api(`/api/debts?ledger=${lid()}&status=all`);
  const d = rows.find(x => x.id === id);
  if(!d){ msg("记录不存在", false); return; }
  debtEditId = id;
  $("#debtTitle").textContent = "编辑（" + (DIR_CN[d.direction] || d.direction) + "）";
  $("#db_dir").value = d.direction;
  $("#db_person").value = d.person;
  $("#db_amt").value = (d.amount/100).toFixed(2);
  $("#db_date").value = d.date;
  $("#db_due").value = d.due_date || "";
  $("#db_note").value = d.note || "";
  $("#debtMask").style.display = "flex";
}
function debtClose(){ $("#debtMask").style.display = "none"; }
async function debtSave(){
  const body = {
    direction: $("#db_dir").value,
    person: $("#db_person").value.trim(),
    amount: Math.round(parseFloat($("#db_amt").value || "0") * 100),
    date: $("#db_date").value,
    due_date: $("#db_due").value || null,
    note: $("#db_note").value.trim() || null,
  };
  if(!body.person){ msg("借出/入人不能为空", false); return; }
  if(!(body.amount > 0)){ msg("金额须大于 0", false); return; }
  if(!body.date){ msg("请选时间", false); return; }
  try{
    if(debtEditId) await apiSend(`/api/debts/${debtEditId}`, "PUT", body);
    else await apiSend(`/api/debts?ledger=${lid()}`, "POST", body);
  }catch(e){ msg("保存失败：" + e.message, false); return; }
  msg("已保存");
  debtClose();
  loadDebts();
}
async function debtDel(id, person){
  if(!(await uiConfirm(`删除「${person}」的这笔记录？还款明细一并删除，不可恢复。`, "删除"))) return;
  try{ await api(`/api/debts/${id}`, {method:"DELETE"}); }
  catch(e){ msg("删除失败：" + e.message, false); return; }
  msg("已删除");
  loadDebts();
}

/* ---- 还款 ---- */
function repayOpen(id, person, dir, remaining){
  repayDebtId = id;
  $("#repayTitle").textContent = `还款 · ${DIR_CN[dir] || dir} ${person}`;
  $("#repayInfo").textContent = `未还余额 ${curSym()}${fmt(remaining)}`;
  $("#rp_amt").value = (remaining/100).toFixed(2);   // 默认全额还清，可改
  $("#rp_date").value = today();
  $("#rp_note").value = "";
  $("#repayMask").style.display = "flex";
}
function repayClose(){ $("#repayMask").style.display = "none"; }
async function repaySave(){
  const amount = Math.round(parseFloat($("#rp_amt").value || "0") * 100);
  if(!(amount > 0)){ msg("还款金额须大于 0", false); return; }
  if(!$("#rp_date").value){ msg("请选还款时间", false); return; }
  try{
    await apiSend(`/api/debts/${repayDebtId}/repays`, "POST",
                  {amount, date: $("#rp_date").value, note: $("#rp_note").value.trim() || null});
  }catch(e){ msg("保存失败：" + e.message, false); return; }
  msg("已记录还款");
  repayClose();
  loadDebts();
}

/* ---- 还款明细 ---- */
async function repaysOpen(id, person, dir){
  repaysDebt = id;
  $("#repaysTitle").textContent = `还款明细 · ${DIR_CN[dir] || dir} ${person}`;
  $("#repaysList").innerHTML = '<div class="sub">加载中…</div>';
  $("#repaysMask").style.display = "flex";
  await repaysRender();
}
function repaysClose(){ $("#repaysMask").style.display = "none"; }
async function repaysRender(){
  let rows = [];
  try{ rows = await api(`/api/debts/${repaysDebt}/repays`); }
  catch(e){ msg("加载失败：" + e.message, false); return; }
  $("#repaysList").innerHTML = rows.length ? rows.map(r => `
    <div class="row" style="justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line, #eee)">
      <span>${esc(r.date)} · ${curSym()}${fmt(r.amount)}${r.note ? ' <span class="sub">' + esc(r.note) + "</span>" : ""}</span>
      <button class="btn btn-ghost" style="padding:2px 8px;font-size:12px" onclick="repayDel(${r.id})">删</button>
    </div>`).join("")
    : '<div class="sub">还没有还款记录</div>';
}
async function repayDel(rid){
  if(!(await uiConfirm("删除这笔还款记录？状态会自动回退为未还清。", "删除"))) return;
  try{ await api(`/api/repays/${rid}`, {method:"DELETE"}); }
  catch(e){ msg("删除失败：" + e.message, false); return; }
  msg("已删除");
  repaysRender();
  loadDebts();
}
