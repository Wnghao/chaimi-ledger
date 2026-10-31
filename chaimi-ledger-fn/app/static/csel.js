/* ===== 自绘下拉组件（全站 <select> 统一升级） =====
   背景：原生 select 弹开的选项面板是操作系统渲染的，CSS 管不到——这就是
   "怎么美化还是很丑"的原因。本组件把每个 select 升级成：
     · 一个样式完全可控的按钮（显示当前选中项 + chevron）
     · 一个自绘选项面板（卡片底/悬停/选中勾/分组标题，跟随明暗主题）
   原生 <select> 保留在 DOM 里（视觉隐藏）作为数据源：value 读写、onchange、
   表单逻辑零改动。选项动态变化（innerHTML 重建/程序赋值）通过 MutationObserver
   和全局 change 捕获自动同步。 */
(function(){
  "use strict";

  const CHEV = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
  let openWrap = null;   // 当前展开的 .csel

  function labelOf(sel){
    const o = sel.selectedOptions && sel.selectedOptions[0];
    return o ? o.textContent.trim() : "";
  }

  function makeOpt(sel, o){
    const row = document.createElement("div");
    row.className = "csel-opt" + (o.disabled ? " dis" : "");
    row.textContent = o.textContent;
    row.dataset.value = o.value == null ? "" : o.value;
    if(o.selected) row.classList.add("on");
    row.addEventListener("click", () => {
      if(o.disabled) return;
      sel.value = o.value;
      sel.dispatchEvent(new Event("change", {bubbles:true}));
      syncBtn(sel);
      closeAll();
    });
    return row;
  }

  function buildPanel(sel){
    const panel = document.createElement("div");
    panel.className = "csel-panel";
    panel.setAttribute("role", "listbox");
    [...sel.children].forEach(node => {
      if(node.tagName === "OPTGROUP"){
        const h = document.createElement("div");
        h.className = "csel-group";
        h.textContent = node.label || "";
        panel.appendChild(h);
        [...node.children].forEach(o => panel.appendChild(makeOpt(sel, o)));
      }else if(node.tagName === "OPTION"){
        panel.appendChild(makeOpt(sel, node));
      }
    });
    if(!panel.children.length){
      const e = document.createElement("div");
      e.className = "csel-empty";
      e.textContent = "（无选项）";
      panel.appendChild(e);
    }
    return panel;
  }

  function syncBtn(sel){
    const wrap = sel.closest(".csel");
    if(!wrap) return;
    const txt = wrap.querySelector(".csel-txt");
    const lb = labelOf(sel);
    if(txt) txt.textContent = lb || "未选择";   // value 匹配不到选项（如未设分类）时给灰字兜底
    wrap.classList.toggle("has-val", !!lb);
    if(sel.disabled) wrap.classList.add("dis"); else wrap.classList.remove("dis");
  }

  function closeAll(){
    if(openWrap){
      const p = openWrap.querySelector(".csel-panel");
      if(p) p.remove();
      openWrap.classList.remove("open");
      openWrap = null;
    }
  }

  function openPanel(wrap){
    if(openWrap === wrap){ closeAll(); return; }
    closeAll();
    const sel = wrap.querySelector("select");
    const panel = buildPanel(sel);
    wrap.appendChild(panel);
    wrap.classList.add("open");
    openWrap = wrap;
    // 视口适配：太靠下往上弹；太靠右往左收
    const r = panel.getBoundingClientRect();
    if(r.bottom > innerHeight - 8){
      panel.classList.add("up");
      const r2 = panel.getBoundingClientRect();
      if(r2.top < 8){ panel.classList.remove("up"); panel.style.maxHeight = (innerHeight - 24) + "px"; }
    }
    if(r.right > innerWidth - 8){ panel.style.left = "auto"; panel.style.right = "0"; }
    // 选中项滚进视野
    const on = panel.querySelector(".csel-opt.on");
    if(on) on.scrollIntoView({block:"nearest"});
  }

  function upgradeOne(sel){
    if(sel.dataset.csel || sel.multiple || sel.hasAttribute("size")) return;
    sel.dataset.csel = "1";
    const wrap = document.createElement("span");
    wrap.className = "csel";
    sel.parentNode.insertBefore(wrap, sel);
    // 行内宽度样式搬给外壳（select 本体此后隐藏）
    if(sel.style.width){ wrap.style.width = sel.style.width; sel.style.width = ""; }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "csel-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.innerHTML = '<span class="csel-txt"></span><span class="csel-chev">' + CHEV + "</span>";
    wrap.appendChild(btn);
    wrap.appendChild(sel);
    sel.classList.add("csel-native");
    btn.addEventListener("click", e => { e.stopPropagation(); openPanel(wrap); });
    // 键盘：Enter/Space/↓ 打开，Esc 关闭
    btn.addEventListener("keydown", e => {
      if(["Enter"," ","ArrowDown"].includes(e.key)){ e.preventDefault(); openPanel(wrap); }
    });
    wrap.addEventListener("keydown", e => {
      if(!wrap.classList.contains("open")) return;
      const opts = [...wrap.querySelectorAll(".csel-opt:not(.dis)")];
      let act = wrap.querySelector(".csel-opt.act");
      if(e.key === "Escape"){ closeAll(); }
      else if(e.key === "ArrowDown" || e.key === "ArrowUp"){
        e.preventDefault();
        let i = opts.indexOf(act) + (e.key === "ArrowDown" ? 1 : -1);
        i = Math.max(0, Math.min(opts.length - 1, i));
        opts.forEach(o => o.classList.remove("act"));
        act = opts[i]; if(act){ act.classList.add("act"); act.scrollIntoView({block:"nearest"}); }
      }else if(e.key === "Enter" && act){ act.click(); }
    });
    syncBtn(sel);
  }

  function upgradeAll(root){
    (root || document).querySelectorAll("select").forEach(upgradeOne);
  }

  // 点外部关闭；Esc 关闭
  document.addEventListener("click", e => { if(openWrap && !openWrap.contains(e.target)) closeAll(); });
  document.addEventListener("keydown", e => { if(e.key === "Escape") closeAll(); });
  addEventListener("resize", closeAll);

  // 代码里直接 sel.value = x 赋值（不走事件）时也能同步按钮文案
  const _vdesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  Object.defineProperty(HTMLSelectElement.prototype, "value", {
    get(){ return _vdesc.get.call(this); },
    set(v){ _vdesc.set.call(this, v); if(this.dataset && this.dataset.csel) syncBtn(this); },
    configurable: true
  });

  // 程序赋值 + dispatch change 时同步按钮文案
  document.addEventListener("change", e => {
    if(e.target.tagName === "SELECT" && e.target.dataset.csel) syncBtn(e.target);
  }, true);

  // 动态插入的 select / 重建的 options 自动跟进
  new MutationObserver(muts => {
    for(const m of muts){
      m.addedNodes && m.addedNodes.forEach(n => {
        if(n.nodeType !== 1) return;
        if(n.tagName === "SELECT") upgradeOne(n);
        else if(n.querySelectorAll) n.querySelectorAll("select").forEach(upgradeOne);
      });
      let t = m.target;
      if(t && t.nodeType === 1){
        if(t.tagName === "OPTION" || t.tagName === "OPTGROUP") t = t.parentElement;
        if(t && t.tagName === "SELECT" && t.dataset.csel) syncBtn(t);
      }
    }
  }).observe(document.body || document.documentElement, {childList:true, subtree:true});

  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => upgradeAll());
  else upgradeAll();
  addEventListener("load", () => upgradeAll());
})();
