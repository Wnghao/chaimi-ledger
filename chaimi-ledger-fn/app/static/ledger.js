/* ===== 流水 ===== */
let txPages = 1;

let txRecentDefault = true;

let qTimer = null;
function onQText(){
  $("#q_clear").style.display = $("#q_text").value ? "" : "none";
  clearTimeout(qTimer);
  qTimer = setTimeout(()=>{ txPageNo = 1; loadTx(); }, 350);
}
function onQKey(e){   // 回车：立即搜索（清掉未到点的防抖）且不换行
  if(e.key === "Enter"){
    e.preventDefault();
    clearTimeout(qTimer);
    $("#q_text").blur();
    txPageNo = 1; loadTx();
  }
}
function clearQText(){ $("#q_text").value = ""; $("#q_clear").style.display = "none"; txPageNo = 1; loadTx(); }
// 待确认流水不满一页时直接落空显示「暂无流水」（搜索 onQText/clearQText 一直有重置，筛选没有）
function setTypeFilter(b){ curTypeFilter=b.dataset.ft; txPageNo=1; document.querySelectorAll("#tab-list .fchip[data-ft]").forEach(x=>x.classList.toggle("on",x===b)); loadTx(); }
async function loadTx(){
  $("#ftBase").textContent = txRecentDefault ? "最近" : "全部";
  const q = new URLSearchParams({ledger:lid(), page:txPageNo, page_size: txRecentDefault ? 20 : 50});
  const qt = $("#q_text").value.trim();
  // 最近态无翻页（纯速览），仅搜索时出底部翻页（#txPager）以便看全匹配结果。
  const showPager = !txRecentDefault || !!qt;
  document.querySelectorAll("#tab-list .tx-type-only").forEach(x=>x.style.display = txRecentDefault ? "none" : "");
  $("#txPager").style.display = showPager ? "" : "none";
  $("#tab-list").style.paddingBottom = showPager ? "80px" : "";
  if(qt) q.set("q", qt);
  // 跨月份的导入流水都可能待确认，忽略时间范围，否则列表为空
  const confirmOnly = curTypeFilter === "confirm";
  if(confirmOnly) q.set("need_confirm","1");
  else{
    if(curTypeFilter) q.set("type", curTypeFilter);
    // 用过范围选择器后按所选范围（范围=「全部」时仍不带 from/to）。
    // 顶部汇总条始终走范围口径（loadSumbarList），与列表态无关
    if(!txRecentDefault && appScope() !== "all"){
      const r = statRange || curStatRange();
      q.set("from", r.from);
      // 自定义区间末端含当天（后端膨胀 23:59:59）；月/年的 to 为下一周期排他界，走 to_exclusive=1
      //（旧版补 " 00:00:00" 会把 10 位纯日期的边界日手工流水多算进来）
      q.set("to", r.to);
      if(appScope() !== "custom") q.set("to_exclusive", "1");
    }
  }
  let r = await api("/api/transactions?"+q);
  // 会停在不存在的页上，指示器出现「N 笔 · 第 X 页」却列空——回拉到末页重查一次
  let pages = Math.max(1, Math.ceil(r.total / (r.page_size || 50)));
  if(txPageNo > pages){
    txPageNo = pages;
    q.set("page", txPageNo);
    r = await api("/api/transactions?"+q);
  }
  txPages = pages;
  $("#txPage").textContent = `${r.total} 笔 · 第 ${r.page} 页`;
  // 按日分组
  const groups = {};
  r.items.forEach(t=>{ const d=(t.date||"").slice(0,10); (groups[d]=groups[d]||[]).push(t); });
  // 退款对：已被退款「吸收」的原交易不再单独渲染（在退款行的双卡片里展示）。
  // 必须全局构建（跨日场景：原交易在 A 日、退款在 B 日，按日分组后 B 日看不到原交易，
  const absorbed = absorbedIds(r.items);
  $("#txList").innerHTML = Object.entries(groups).map(([d,items])=>{
    return `
    <div style="padding:6px 6px 0">
      <div class="day-head"><span>${d}</span><span class="num">${daySum(items, absorbed)}</span></div>
      <div class="card" style="padding:6px">${items.filter(x=>!absorbed.has(x.id)).map(txRow).join("")}</div>
    </div>`;
  }).join("") ||
    `<div class="empty"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/><line x1="8" x2="21" y1="18" y2="18"/></svg>暂无流水</div>`;
  txSelDecorate();
}
function netSum(items, absorbed){
  let e = 0, i = 0;
  const refundByOrigin = {};
  items.forEach(t=>{ if(t.refund_for) refundByOrigin[t.refund_for] = t; });
  items.forEach(t=>{
    if(absorbed && absorbed.has(t.id)){
      const r = refundByOrigin[t.id];
      if(r){
        // 净额 = signed(原) + signed(退款)：同号相加，原支出 100 + 退款入账 100 = 0；半退则留差额
        const net = signed(t.type, t.amount) + signed(r.type, r.amount);
        if(net < 0) e += -net;       // 净支出
        else if(net > 0) i += net;   // 净收入（罕见：退款大于原消费）
      }
    } else if(t.type === "expense"){
      e += t.amount;
    } else if(t.type === "income"){
      i += t.amount;
    }
  });
  return {e, i};
}
function daySum(items, absorbed){
  const {e, i} = netSum(items, absorbed);
  return `支出 ${fmt(e)} · 收入 ${fmt(i)}`;
}
async function txPage(d){
  const target = Math.min(Math.max(1, txPageNo + d), txPages);
  if(target === txPageNo) return;
  txPageNo = target;
  await loadTx();
  // .main 是滚动容器（body 锁滚动），--gbar-h 为固定顶栏实测高度，
  // 让出该高度后筛选栏恰好落在顶栏下方，列表首行紧随其后
  const sc = $(".main"), el = $("#tab-list .tx-filter");
  const gbarH = parseFloat(getComputedStyle(document.body).getPropertyValue("--gbar-h")) || 96;
  const top = el.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop - gbarH;
  sc.scrollTo({top:Math.max(0,top), behavior:"smooth"});
}
async function exportReceipts(){
  const f = dtGet($("#exp_from")), t = dtGet($("#exp_to"));
  let url = `api/receipts/export?ledger=${lid()}`;
  if(f) url += `&from=${f}`;
  if(t) url += `&to=${t}`;
  try{
    const r = await fetch(url);
    if(!r.ok){
      const e = await r.json().catch(() => ({}));
      return msg(e.detail || "下载失败", false);
    }
    const blob = await r.blob();
    // 文件名：chaimi-ledger-账本名小票-日期.zip（账本名去掉图标前缀）
    const lg = await curLedger();
    const t = new Date();
    const stamp = `${t.getFullYear()}${pad2(t.getMonth()+1)}${pad2(t.getDate())}`;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `chaimi-ledger-${(lg && lg.name || "").trim() || "账本"}小票-${stamp}.zip`;
    a.click();
    URL.revokeObjectURL(a.href);
    msg("小票已打包下载 ✅");
  }catch(e){ msg("下载失败：" + e.message, false); }
}
function exportCsv(fmt="csv"){
  const f = dtGet($("#exp_from")), t = dtGet($("#exp_to"));
  let url = `api/export?ledger=${lid()}&format=${fmt}`;
  if(f) url += `&from=${f}`;
  if(t) url += `&to=${t}`;
  location.href = url;
}
// 导出页快捷区间：全部/本月/本年（默认全部=数据库流水起始日至今天）
function expInitRange(){ expSetRange("all"); }
async function expSetRange(mode){
  let f = today(), t = today();   // 兜底：库为空时今天~今天
  if(mode === "month") f = thisMonth() + "-01";
  else if(mode === "year") f = thisMonth().slice(0, 4) + "-01-01";
  else try{   // all：查最早流水月
    const trend = await api(`/api/stats/trend?ledger=${lid()}&from=2000-01-01&to=2100-01-01`);
    if(trend.length) f = trend[0].month + "-01";
  }catch(_){}
  dtSet($("#exp_from"), f); dtSet($("#exp_to"), t);
  document.querySelectorAll(".exp-presets .btn").forEach(b =>
    b.classList.toggle("on", b.dataset.mode === mode));
}

/* ===== 编辑/确认弹窗 ===== */
let curDay = "";
let dayRefresh = null;
function closeDay(){ $("#dayMask").classList.remove("on"); curDay = ""; dayRefresh = null; }
function viewReceipt(p){ if(!p) return; $("#rcpImg").src = p.replace(/^\//,"").split("?")[0] + "?t=" + Date.now(); $("#rcpMask").classList.add("on"); }
function closeReceipt(){ $("#rcpMask").classList.remove("on"); $("#rcpImg").src = ""; closeCrop(); }

/* ===== 小票手动裁剪 ===== */
let cropPath = "", cropRect = null;   // cropRect = {x0,y0,x1,y1} 最终选区（屏幕坐标）
function openCrop(){
  cropPath = $("#rcpImg").src;
  if(!cropPath) return;
  $("#cropImg").src = cropPath;
  $("#cropMask").style.display = "flex";
  $("#cropSel").style.display = "none";
  $("#cropDim").style.display = "none";
  $("#cropOk").disabled = true;
  cropRect = null;
  let start = null;
  const wrap = $("#cropWrap");
  wrap.onpointerdown = e => { e.preventDefault(); start = cropPos(e); cropRect = null; };
  wrap.onpointermove = e => {
    if(!start) return;
    e.preventDefault();
    const end = cropPos(e);
    const x0 = Math.min(start.x, end.x), y0 = Math.min(start.y, end.y);
    const w = Math.abs(end.x - start.x), h = Math.abs(end.y - start.y);
    cropRect = {x0, y0, x1: x0 + w, y1: y0 + h};
    const sel = $("#cropSel");
    sel.style.display = "block";
    sel.style.left = x0 + "px"; sel.style.top = y0 + "px";
    sel.style.width = w + "px"; sel.style.height = h + "px";
    const dim = $("#cropDim");
    dim.style.display = "block";
    dim.style.clipPath = `polygon(0 0,100% 0,100% 100%,0 100%,0 0,${x0}px 0,${x0}px ${y0+h}px,${x0+w}px ${y0+h}px,${x0+w}px ${y0}px,${x0}px ${y0}px)`;
    $("#cropOk").disabled = w < 20 || h < 20;
  };
  wrap.onpointerup = () => { start = null; };   // 只清 start，cropRect 保留供 applyCrop 用
}
function closeCrop(){ $("#cropMask").style.display = "none"; cropRect = null; }
function cropPos(e){
  const r = $("#cropImg").getBoundingClientRect();
  return {x: Math.max(0, Math.min(e.clientX - r.left, r.width)),
          y: Math.max(0, Math.min(e.clientY - r.top, r.height))};
}
async function applyCrop(){
  if(!cropRect || !cropPath) return;
  const img = $("#cropImg");
  const scaleX = img.naturalWidth / img.getBoundingClientRect().width;
  const scaleY = img.naturalHeight / img.getBoundingClientRect().height;
  const x = Math.round(cropRect.x0 * scaleX);
  const y = Math.round(cropRect.y0 * scaleY);
  const w = Math.round((cropRect.x1 - cropRect.x0) * scaleX);
  const h = Math.round((cropRect.y1 - cropRect.y0) * scaleY);
  const name = cropPath.split("/").pop().split("?")[0];
  const btn = $("#cropOk"); btn.disabled = true; btn.textContent = "裁剪中…";
  try{
    await apiSend(`/api/receipts/crop?path=${encodeURIComponent(name)}`, "POST", {x, y, w, h});
    msg("已裁剪 ✅");
    closeCrop();
    const bust = `?t=${Date.now()}`;
    const clean = cropPath.split("?")[0];
    $("#rcpImg").src = clean + bust;
    document.querySelectorAll(`img[src*="${name}"]`).forEach(el => {
      el.src = el.src.split("?")[0] + bust;
    });
  }catch(e){ msg("裁剪失败：" + e.message, false); }
  finally{ btn.disabled = false; btn.textContent = "确认裁剪"; }
}
function showDetail(title, html, actions){
  $("#dayTitle").textContent = title;
  $("#daySummary").textContent = "";
  const a = $("#dayActions");
  a.style.display = actions ? "block" : "none";
  a.innerHTML = actions || "";
  $("#dayMask").classList.add("on");
  $("#dayList").innerHTML = html;
}
function renderDetail(items){
  const absorbed = absorbedIds(items);
  const {e, i} = netSum(items, absorbed);
  $("#daySummary").innerHTML =
    e && i ? `<span class="c-exp">支出 ${fmt(e)}</span>&nbsp;·&nbsp;<span class="c-inc">收入 ${fmt(i)}</span>`
    : e ? `<span class="c-exp">支出 ${fmt(e)}</span>`
    : i ? `<span class="c-inc">收入 ${fmt(i)}</span>` : "";
  return `<div class="sub" style="margin-bottom:6px">支出 ${fmt(e)} · 收入 ${fmt(i)} · 点击编辑</div>` +
    (items.filter(x=>!absorbed.has(x.id)).map(txRow).join("") || '<div class="sub">暂无流水</div>');
}
async function fillDayDetail(fetcher){   // 明细弹窗统一取数渲染（日/区间/分类/人情四个入口共用）
  try{
    const r = await fetcher();
    $("#dayList").innerHTML = renderDetail(r.items);
  }catch(e){
    $("#dayList").innerHTML = `<div class="sub">加载失败：${esc(e.message)}</div>`;
  }
}
async function showDay(dd){
  curDay = dd;
  dayRefresh = () => showDay(dd);
  showDetail(`${(+dd.slice(5,7))}月${(+dd.slice(8))}日 流水`, '<div class="sub">加载中…</div>',
    `<button class="btn btn-pri" style="width:100%" onclick="openAdd('${dd}')">＋ 记一笔</button>`);
  await fillDayDetail(()=>api(`/api/transactions?ledger=${lid()}&from=${dd}&to=${dd}&page=1&page_size=100`));
}
/* 图表/卡片下钻：拉流水明细 */
async function showTxDetail(title, params){
  curDay = "";
  dayRefresh = () => showTxDetail(title, params);
  showDetail(title, '<div class="sub">加载中…</div>');
  // to 语义二选一：toExclusive=排他上界（下一周期首日裸日期，to_exclusive=1 服务端原样比较，
  // 对 10 位纯日期与 19 位带时间流水都正确排他）；默认=含当天的结束日（10 位裸日期，
  // 服务端膨胀为当天 23:59:59）。旧版补 " 00:00:00" 会把 10 位的边界日手工流水多算进来。
  const q = new URLSearchParams({ledger:lid(), page:1, page_size:100});
  for(const [k, v] of Object.entries(params)){ if(k !== "toExclusive") q.set(k, v); }
  if(params.toExclusive) q.set("to_exclusive", "1");
  await fillDayDetail(()=>api("/api/transactions?"+q));
}
function showMonthDetail(month, type){
  const to = nextMonthFirst(month);
  showTxDetail(`${month} ${type==="income"?"收入":type==="expense"?"支出":"全部"}明细`,
    {from: month+"-01", to, toExclusive:true, ...(type?{type}:{})});
}
async function showCategoryDetail(catName, range, type){
  dayRefresh = () => showCategoryDetail(catName, range, type);
  const rg = range || statRange || curStatRange(dtGet($("#monthSel")) || thisMonth());
  const flow = type || "expense";
  showDetail(`${rg.label} · ${catName} 明细`, '<div class="sub">加载中…</div>');
  // 旧的「拉区间前 200 条再前端过滤」在区间流水多时会漏掉该分类的流水。
  const top = categories.find(c=>c.name===catName && !c.parent_id && c.type===flow)
           || categories.find(c=>c.name===catName && !c.parent_id);
  if(!top){ $("#dayList").innerHTML = '<div class="sub">暂无流水</div>'; return; }
  // to 语义与 showTxDetail 一致：toExclusive / 月/年/全部（下一周期排他界）→ to_exclusive=1 裸日期；
  // 仅自定义区间的 to 是含当天的结束日 → 裸日期交服务端膨胀 23:59:59
  const excl = rg.toExclusive || appScope() !== "custom";
  await fillDayDetail(()=>api(`/api/transactions?ledger=${lid()}&from=${rg.from}&to=${encodeURIComponent(rg.to)}`
    + `${excl ? "&to_exclusive=1" : ""}&category=${top.id}&page=1&page_size=500`));
}
function openAdd(dateStr){   // 新增交易：预填日期（日历日视图传入 dd），类型默认支出
  txOpen("create", {date: dateStr});
}
async function openEdit(tid){   // 编辑/待确认：载入该笔到统一弹窗
  const t = txCache[tid]; if(!t) return;
  const mode = t.need_confirm ? "confirm" : "edit";
  txOpen(mode, {tx:t,
    items:[{type:t.type, amount:t.amount, category_id:t.category_id, account_id:t.account_id,
      date:(t.date||"").slice(0,19), name:t.name||"", note:t.note||"", order_no:t.order_no||"", tags:t.tags||{}}]});
  if(t.type === "transfer" && (mode === "confirm" || t.refund_for)){
    // 近 50 笔支出里同日同额排最前；当前关联的原消费置顶（旧单可能不在近 50 笔里）
    $("#e_refund_row").style.display = "block";
    const day = (t.date||"").slice(0,10);
    const isM = x=>x.amount === t.amount && (x.date||"").slice(0,10) === day;
    const list = [...await fetchExpenseCands(day)].sort((a,b)=>isM(b)-isM(a));
    // 也不作默认选中——按「原始订单 ≤ 退款当天」口径视为未关联，确认/保存时下发 0 清掉旧链
    const lateOrigin = t.refund_origin && (t.refund_origin.date||"").slice(0,10) > day;
    if(t.refund_origin && !lateOrigin && !list.some(x=>x.id === t.refund_origin.id)) list.unshift(t.refund_origin);
    renderRefundDD(list, lateOrigin ? 0 : (t.refund_for || 0), day);
  }else if(t.type === "income"){
    const day = (t.date||"").slice(0,10);
    const sameAmt = (await fetchExpenseCands(day, `&from=${day}`)).filter(x=>x.amount === t.amount && x.id !== t.id);
    if(sameAmt.length){
      $("#e_refund_row").style.display = "block";
      renderRefundDD(sameAmt, t.refund_for || 0, day);
    }
  }
}
let editRefundItems = [], editRefundSel = 0, editRefundDefault = [], editRefundCap = "";

function txItemCatChange(sel){
  const item = sel.closest(".txf-item"); if(!item) return;
  const cat = categories.find(c=>c.id === +sel.value);
  const wantTransfer = !!cat && cat.type === "transfer";
  const isTransfer = item.dataset.type === "transfer";
  if(wantTransfer && isTransfer){ refundRowSync(item); return; }   // 组内切换（退款⇄不计收支）：只联动关联行
  if(isTransfer && !wantTransfer){
    if(editTx && editTx.refund_for){   // 已关联退款的流水须保持不计收支（与类型 chip 同一条校验）
      msg("该流水已关联退款，须保持不计收支；请先在下方取消关联再改类型", false);
      return txItemRevertCat(item);
    }
    item.dataset.type = item.dataset.prevType || "expense";
  }else if(wantTransfer){
    item.dataset.prevType = item.dataset.type;
    item.dataset.type = "transfer";
  }else return;
  txItemSyncTypeRow(item);
  refundRowSync(item);
}
function txItemRevertCat(item){   // 校验拦下后把分类下拉拉回 transfer 组（保持与类型一致，选中「退款」）
  const rid = (categories.find(c=>c.type==="transfer" && c.name==="退款") || {}).id;
  item.querySelector(".txf-cat").innerHTML = txCatOptions("transfer", rid);
}
function txItemSyncTypeRow(item){   // 翻转后刷新类型行 chip 选中态 / 新建分类按钮 / 分类下拉选项
  const type = item.dataset.type;
  item.querySelectorAll(".txf-types .fchip").forEach(b=>b.classList.toggle("on", b.dataset.t === type));
  const cs = item.querySelector(".txf-cat");
  cs.innerHTML = txCatOptions(type, +cs.value);
  item.querySelector(".txf-newcat").style.display = type === "transfer" ? "none" : "";
}
async function refundRowSync(item){
  // 单笔弹窗里：transfer + 分类=退款 → 显示「关联到原消费」并按当前填的日期拉封顶候选；否则收起。
  // 候选按拉取瞬间的日期封顶；之后再改日期需重选一次分类刷新（与编辑弹窗打开时拉一次同口径）
  const cat = categories.find(c=>c.id === +item.querySelector(".txf-cat").value);
  const on = item.dataset.type === "transfer" && cat && cat.name === "退款"
             && document.querySelectorAll("#txItems .txf-item").length === 1;
  if(!on){ $("#e_refund_row").style.display = "none"; return; }
  $("#e_refund_row").style.display = "block";
  const day = (item.querySelector(".txf-date").value || today()).slice(0,10);
  renderRefundDD(await fetchExpenseCands(day), txMode === "create" ? 0 : (editRefundSel || 0), day);
}
function nextDayStr(day){   // 裸日期 +1 天：作 to_exclusive=1 的排他上界，等效「≤ day 含当天」（本地拼接，避免 toISOString 的 UTC 偏移）
  const nd = new Date(day + "T00:00:00"); nd.setDate(nd.getDate()+1);
  return `${nd.getFullYear()}-${pad2(nd.getMonth()+1)}-${pad2(nd.getDate())}`;
}
// extra 附加 &from=（收入分支限同日）或 &q=（下拉搜索）
async function fetchExpenseCands(day, extra){
  const r = await api(`/api/transactions?ledger=${lid()}&type=expense${extra||""}&to=${nextDayStr(day)}&to_exclusive=1&page=1&page_size=50`);
  return r.items;
}
document.addEventListener("click", e=>{
  if(e.target.id === "e_refund_dd"){ closeRefundDD(); return; }
  if(!(e.target.closest && e.target.closest("#e_refund_dd"))) closeRefundDD();
});
window.addEventListener("resize", ()=>{
  // 旧版此处无条件 closeRefundDD 会把聚焦中的搜索框 display:none 夺焦、输入法立即收起
  positionRefundDD();
  // 窗口变宽回到常驻侧栏布局时，清掉抽屉推挤状态（matchMedia 与 CSS 同口径，见 toggleSidebar 注释）
  if(matchMedia("(min-width:769px)").matches){ $("#sidebar").classList.remove("open"); $("#sideMask").classList.remove("on"); $(".main").classList.remove("pushed"); }
});
if(window.visualViewport) visualViewport.addEventListener("resize", positionRefundDD);   // iOS：输入法只动 visualViewport，不触发 window resize
function closeRefundDD(){ $("#e_refund_dd") && $("#e_refund_dd").classList.remove("open"); }
function toggleRefundDD(e){
  e && e.stopPropagation();
  const dd = $("#e_refund_dd");
  if(dd.classList.contains("open")){ closeRefundDD(); return; }
  renderRefundSelf();
  dd.classList.add("open");
  positionRefundDD();
}
function renderRefundSelf(){   // 弹窗顶卡=本笔退款交易：编辑/确认取 editTx，新建取条目表单当前值
  let name = "退款", amount = 0, sub = [];
  if(editTx){
    name = editTx.name || "退款"; amount = editTx.amount || 0;
    sub = [(editTx.date || "").slice(0, 16), editTx.note || "", editTx.order_no || ""];
  }else{
    const item = document.querySelector("#txItems .txf-item");
    if(item){
      name = (item.querySelector(".txf-name").value || "").trim() || "退款";
      amount = Math.round(parseFloat(item.querySelector(".txf-amt").value || "0") * 100);
      sub = [(item.querySelector(".txf-date").value || "").replace("T", " "),
             (item.querySelector(".txf-note").value || "").trim(),
             (item.querySelector(".txf-order").value || "").trim()];
    }
  }
  $("#rf_self").innerHTML = `<div class="rf-body">
      <div class="tx-note">本笔退款：${esc(name)}</div>
      <div class="sub tx-sub">${esc(sub.filter(Boolean).join(" · "))}</div>
    </div>
    <div class="tx-amt num c-inc">+${fmt(amount)}</div>`;
}
function positionRefundDD(){
  const dd = $("#e_refund_dd");
  if(dd && dd.classList.contains("open"))
    $("#rf_sheet").style.maxHeight = Math.round((window.visualViewport ? visualViewport.height : innerHeight) * 0.9) + "px";
}
function refundCard(x){
  // 无名称时备注顶到首行，第二行不再重复；字段一律转义（账单派生文本）
  const note = x.note || "";
  return `<div class="rf-body">
      <div class="tx-note">${esc((x.name||note||"").slice(0,40))}</div>
      <div class="sub tx-sub">${[(x.date||"").slice(0,16), x.name ? esc(note) : "", esc(x.order_no||"")].filter(Boolean).join(" · ")}</div>
    </div>
    <div class="tx-amt num c-exp">−${fmt(x.amount)}</div>`;
}
function renderRefundDD(items, selected, capDay){
  _rfQuery = "";   // 每次打开重置搜索词，避免上次残留与默认列表不一致
  editRefundCap = (capDay||"").slice(0,10);
  editRefundDefault = items; editRefundItems = items; editRefundSel = selected;
  $("#e_refund_opts").innerHTML =
    `<div class="ai-box rf-qbox" onclick="event.stopPropagation()">
       <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="flex:0 0 16px;opacity:.55"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
       <input id="rf_q" placeholder="搜索全部支出：名称/备注/金额/商户/订单号" value="${esc(_rfQuery)}" oninput="refundSearch(this.value)">
     </div><div id="rf_list"></div>`;
  renderRefundList(false);
  renderRefundCur();
  closeRefundDD();
}
function renderRefundList(searching){
  const hint = searching ? `搜索结果 ${editRefundItems.length} 笔` : "同日同额匹配优先；找不到时输入关键词查全部支出";
  $("#rf_list").innerHTML = `<div class="rf-hint sub">${hint}</div>`
    + `<div class="rf-item ${!editRefundSel?"on":""}" onclick="setRefundDD(0,event)"><div class="rf-body sub">不关联</div></div>`
    + editRefundItems.map(x=>`<div class="rf-item ${x.id===editRefundSel?"on":""}" onclick="setRefundDD(${x.id},event)">${refundCard(x)}</div>`).join("");
}
let _rfQuery = "", _rfTimer = null;
async function refundSearch(q){   // 空关键词回默认匹配列表；否则防抖搜全部支出（名称/备注/商户/订单号）
  _rfQuery = q;
  clearTimeout(_rfTimer);
  if(!q.trim()){ editRefundItems = editRefundDefault; renderRefundList(false); return; }
  _rfTimer = setTimeout(async ()=>{
    try{
      editRefundItems = await fetchExpenseCands(editRefundCap, `&q=${encodeURIComponent(q.trim())}`);
      renderRefundList(true);
    }catch(_){}
  }, 250);
}
function setRefundDD(id, e){
  e && e.stopPropagation();
  editRefundSel = id;
  document.querySelectorAll("#e_refund_opts .rf-item").forEach(x=>x.classList.remove("on"));
  e && e.currentTarget && e.currentTarget.classList.add("on");
  renderRefundCur();
  closeRefundDD();
}
function renderRefundCur(){
  const x = editRefundItems.find(i=>i.id===editRefundSel);
  $("#e_refund_cur").innerHTML = x ? refundCard(x) : `<div class="rf-body sub">不关联</div>`;
}
async function saveEditTx(confirm){
  const t = editTx; if(!t) return;
  const it = txCollect()[0] || {};
  if(!it.amount) return msg("金额不能为空", false);
  if(t.refund_for && (it.type || t.type) !== "transfer")
    return msg("该流水已关联退款，须保持不计收支；请先在下方取消关联再改类型", false);
  const tags = Object.assign({}, t.tags||{});   // 保留导入时已有的标签
  // 保留原始归属（导入流水的 liulu 明细不被「老公」覆盖，统计组内仍见各平台构成）
  const origRaw = tags.attribution || "";
  const origShow = origRaw ? (raw2group[origRaw] || origRaw) : "";
  const finalAttr = it.attr && it.attr === origShow ? (origRaw || it.attr) : it.attr;
  if(finalAttr) tags.attribution = finalAttr; else delete tags.attribution;
  const rfVis = $("#e_refund_row").style.display !== "none";   // 关联下拉可见=本笔是退款关联场景
  const body = {type:it.type || t.type, amount:it.amount, category_id:it.category_id,
    account_id:it.account_id, to_account_id:t.to_account_id||null,
    name:it.name,
    order_no:it.order_no,
    refund_for: rfVis ? (editRefundSel||0) : null,
    date:it.date, note:it.note, tags,
    // receipt_path：null=未改（COALESCE 保留），""=清空（NULLIF 转 NULL），非空=覆盖
    receipt_path: eRcp === null ? null : eRcp,
    need_confirm: !!t.need_confirm};
  try{
    await apiSend(`/api/transactions/${t.id}`, "PUT", body);
    if(confirm){
      const rb = {learn:true};
      if(it.category_id) rb.category_id = it.category_id;
      if(it.account_id && it.account_id !== t.account_id) rb.account_id = it.account_id;
      if(rfVis) rb.refund_for = editRefundSel || 0;
      await apiSend(`/api/pending/${t.id}/resolve`, "POST", rb);
      msg("已确认 ✅");
    }else{
      msg("已保存 ✅");
    }
    txClose(); afterTxWrite();
  }catch(e){
    msg("保存失败："+e.message, false);
  }
}
async function deleteEditTx(){
  const t = editTx; if(!t) return;
  if(!await uiConfirm("确定删除这笔交易？", "删除")) return;
  await api(`/api/transactions/${t.id}`, {method:"DELETE"});
  aiPurgeTxCard(t.id);
  msg("已删除"); txClose(); afterTxWrite();
}

/* ===== 人情往来 ===== */async function loadRenqing(){
  const rq = await api(`/api/stats/renqing?ledger=${lid()}`);
  document.querySelector("#renqingTable tbody").innerHTML = rq.map(r=>`<tr style="cursor:pointer" onclick="showPersonDetail('${qEsc(r.person)}')">
    <td><b>${esc(r.person)}</b></td>
    <td class="num c-exp" style="text-align:right">${fmt(r.given)}</td>
    <td class="num c-inc" style="text-align:right">${fmt(r.received)}</td>
    <td class="num" style="text-align:right;font-weight:600;color:${r.balance>0?"var(--expense)":r.balance<0?"var(--income)":"var(--muted)"}">${r.balance>0?"+"+fmt(r.balance):r.balance<0?"−"+fmt(-r.balance):"0"}</td></tr>`).join("");
  $("#renqingEmpty").style.display = rq.length ? "none" : "block";
  document.getElementById("renqingTable").style.display = rq.length ? "" : "none";
}
async function showPersonDetail(person){
  dayRefresh = () => showPersonDetail(person);
  showDetail(`${person} · 人情明细`, '<div class="sub">加载中…</div>');
  await fillDayDetail(()=>api(`/api/transactions?ledger=${lid()}&page=1&page_size=200&renqing_name=${encodeURIComponent(person)}`));
}

let txSelMode = false;
const txSelSet = new Set();

function txSelEnter(){
  txSelMode = true; txSelSet.clear();
  txSelVis(true);
  txSelDecorate();
}
// 退出选择态：refresh=false 供已另行触发全局刷新的路径（afterTxWrite 内含 loadTx）复用
function txSelExit(refresh = true){
  txSelMode = false; txSelSet.clear();
  txSelVis(false);
  if(refresh) loadTx();   // 重渲染去掉勾选框、恢复点击编辑
}
// 选择态显隐切换：工具条上、筛选行（含编辑按钮）与搜索框下
function txSelVis(on){
  $("#txSelBar").style.display = on ? "flex" : "none";
  document.querySelector("#tab-list .tx-filter .filters").style.display = on ? "none" : "";
  document.querySelector("#txSearchBox").style.display = on ? "none" : "";
}
// 当前页卡片 id（全选与全选框状态共用；已被退款吸收的原交易不渲染，天然不在内）
function txSelPageIds(){
  return [...document.querySelectorAll("#txList .tx-card[data-txid]")].map(c=>+c.dataset.txid);
}
// 渲染后装饰（loadTx 末尾调用，DOM 每次都是新渲染）：给每张卡插勾选框并接管点击
function txSelDecorate(){
  if(!txSelMode) return;
  document.querySelectorAll("#txList .tx-card").forEach(card=>{
    const id = +card.dataset.txid;
    if(!id) return;
    const cb = document.createElement("label");
    cb.className = "tx-sel";
    cb.innerHTML = `<input type="checkbox" ${txSelSet.has(id) ? "checked" : ""}>`;
    cb.onclick = e => e.stopPropagation();
    cb.querySelector("input").onchange = e => txSelToggle(id, e.target.checked);
    card.insertBefore(cb, card.firstChild);
    // 点击=切换勾选：接管卡片 onclick；双行卡里原交易行自带独立 onclick 也一并接管。
    // 注意不能给普通 .tx-row 也挂——行处理器+冒泡到卡片处理器会切换两次，净效果=没选中
    const toggle = () => { const i = cb.querySelector("input"); i.checked = !i.checked; txSelToggle(id, i.checked); };
    card.onclick = toggle;
    card.querySelectorAll(".tx-row[onclick]").forEach(r => r.onclick = toggle);
  });
  txSelSyncUI();
}
function txSelToggle(id, on){
  on ? txSelSet.add(id) : txSelSet.delete(id);
  txSelSyncUI();
}
function txSelToggleAll(on){
  txSelPageIds().forEach(id => on ? txSelSet.add(id) : txSelSet.delete(id));
  document.querySelectorAll("#txList .tx-sel input").forEach(i => i.checked = on);
  txSelSyncUI();
}
function txSelSyncUI(){
  $("#txSelCount").textContent = `已选 ${txSelSet.size} 笔`;
  $("#txSelModBtn").disabled = !txSelSet.size;
  const pageIds = txSelPageIds();
  $("#txSelAll").checked = pageIds.length > 0 && pageIds.every(id => txSelSet.has(id));
}
function txSelCloseModal(){ $("#txSelMask").classList.remove("on"); }
function txSelOpenModal(){
  if(!txSelSet.size) return;
  $("#txSelModHint").textContent = `将对已选 ${txSelSet.size} 笔流水应用以下修改（留「不修改」的项保持原值）`;
  // 分类：按支出/收入分组列出
  const cats = categories.filter(c=>!c.parent_id);
  const grp = t => `<optgroup label="${t==="expense"?"支出":"收入"}">` +
    cats.filter(c=>c.type===t).map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join("") + `</optgroup>`;
  $("#bs_cat").innerHTML = `<option value="">不修改</option>` + grp("expense") + grp("income");
  // 归属：分组名 + 未分组归属（与记账弹窗同口径），另给「清除归属」
  const attrNames = attrNamesAll();
  $("#bs_attr").innerHTML = `<option value="">不修改</option>` +
    (attrNames.length ? `<option value="__clear__">（清除归属）</option>` +
      attrNames.map(a=>`<option value="${esc(a)}">${esc(a)}</option>`).join("") : "");
  $("#bs_acc").innerHTML = `<option value="">不修改</option>` +
    accounts.map(a=>`<option value="${a.id}">${esc(a.name)}</option>`).join("");
  $("#txSelMask").classList.add("on");
}
async function txSelApply(){
  const cat = +$("#bs_cat").value || null;
  const attrRaw = $("#bs_attr").value;
  const attr = attrRaw === "" ? null : (attrRaw === "__clear__" ? "" : attrRaw);
  const acc = +$("#bs_acc").value || null;
  if(cat === null && attr === null && acc === null) return msg("请至少选择一项要修改的内容", false);
  const btn = $("#bs_save"); btn.disabled = true;
  try{
    const r = await apiSend(`/api/transactions/batch_update?ledger=${lid()}`, "POST",
      {ids:[...txSelSet], category_id:cat, attribution:attr, account_id:acc});
    txSelCloseModal();
    msg(`已修改 ${r.updated} 笔 ✅`);
    txSelExit(false);   // 数据已变：afterTxWrite 全局刷新（内含 loadTx），无需再单独 loadTx
    afterTxWrite();
  }catch(e){
    msg("批量修改失败：" + e.message, false);
  }finally{ btn.disabled = false; }
}