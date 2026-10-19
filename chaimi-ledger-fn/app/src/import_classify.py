"""分类瀑布数据与工具：关键词词典（含例外/车牌/加油序）、映射查询、自愈（IMPORT_REFACTOR_PLAN §三）。"""
from __future__ import annotations

import re
import sqlite3

import db as dbm
from import_common import _chunks

# ---------- 分类解析（三段式 + transfer 子类；二级分类已取消） ----------

# 一级分类关键词兜底（微信账单无分类列，靠商品/商户名识别；顺序=优先级；
# 只收高精度词——能跨类误伤的泛化词（如「店」「平台」）一律不收）
TOP_KEYWORD_LEXICON = [
    ("工资薪酬", ["工资", "薪资", "奖金", "报酬", "薪酬", "劳务"]),
    # 红包/礼金合并进「人情往来」（小额通用红包切「红包礼金」，见 dbm.is_small_generic_packet）
    ("人情往来", ["压岁", "红包", "礼金", "随礼", "人情往来",
                  "份子", "人情", "花圈", "奠仪", "伴手礼"]),
    ("美容美发", ["发艺", "理发", "美发", "美容", "美甲", "护肤"]),
    ("医疗健康", ["药房", "药店", "诊所", "医院", "牙科", "体检", "医药", "挂号"]),
    ("加油充电", ["加油", "加油站", "充电桩", "中石化", "中国石化", "电子钱包", "石油"]),
    ("爱车养车", ["停车", "ETC", "etc", "洗车", "捷停车", "速停车", "云停", "JSPAY",
                  "4S店", "维保", "保养", "汽修", "轮胎"]),
    ("交通出行", ["滴滴", "打车", "快车", "顺风车", "出租车", "网约车", "12306", "中铁网络", "铁路",
                  "高铁", "客运站", "汽车客运", "地铁", "公交", "机票", "航空", "机场", "共享单车"]),
    ("充值缴费", ["话费", "宽带", "水费", "电费", "燃气", "物业费", "有线电视", "阿里云", "卡充值",
                  "采暖", "取暖", "暖气"]),
    ("餐饮美食", ["食堂", "餐厅", "餐饮", "吃饭", "早餐", "早饭", "午饭", "午餐", "晚饭", "晚餐", "烧烤", "小吃", "奶茶", "咖啡", "早点", "外卖", "面馆", "火锅", "市集", "农贸市场", "饭馆", "快餐", "小吃部",
                  "饮品", "蛋糕", "烘焙", "甜品", "夜宵"]),   # 餐时词：AI 记一笔高频（早餐5元/打车15 这类），导入侧同样受益
    ("母婴亲子", ["奶粉", "尿裤", "尿不湿", "婴儿", "母婴"]),
    ("日用百货", ["超市", "百货", "连锁", "便利", "生鲜", "五金", "家具", "家居", "建材", "纸巾",
                  "卫生巾", "护垫", "湿巾"]),
    ("服饰装扮", ["男装", "女装", "童装", "服装", "服饰", "羽绒服", "运动鞋", "皮鞋", "鞋店"]),
    ("文化休闲", ["影院", "电影", "游戏", "图书", "KTV", "健身", "景区", "门票", "旅游"]),
    ("教育培训", ["培训", "网课", "教育", "学费", "课程"]),
    ("数码电器", ["数码", "电器", "手机", "电脑", "家电", "耳机", "充电宝"]),
]

_PLATE_RE = re.compile(
    "[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领]"
    "[A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,6}")

def _renqing_split(text: str, amount: int | None) -> str:
    """人情类关键词命中后的两级归类：
    小额通用红包 → 「红包礼金」；其余（大额或带明确人情含义）→ 「人情往来」。"""
    return "红包礼金" if dbm.is_small_generic_packet(text, amount) else "人情往来"

# 拼手气/群红包等确定字样的判定收口在 dbm.renqing_need_confirm（与词表常量同居 db.py）

# 加油词表单列：优先于车牌正则（带车牌的加油记录归加油充电而非爱车养车）
_FUEL_WORDS = dict(TOP_KEYWORD_LEXICON)["加油充电"]

# 产品级例外（优先级最高）：子串撞车的产品名直接定向，防词典误伤
_KW_EXCEPTIONS = [("航空杯", "日用百货")]

def _top_by_keyword(text: str, amount: int | None = None) -> str | None:
    # 优先级：产品例外 > 加油充电 > 车牌正则 > 词典顺序
    for word, cat in _KW_EXCEPTIONS:
        if word in text:
            return cat
    if any(w in text for w in _FUEL_WORDS):
        return "加油充电"
    if _PLATE_RE.search(text):
        return "爱车养车"
    for cat, words in TOP_KEYWORD_LEXICON:
        if any(w in text for w in words):
            # 人情类词表统一按金额/字样分到「红包礼金」或「人情往来」
            return _renqing_split(text, amount) if cat == "人情往来" else cat
    return None

def _resolve_by_mapping(conn: sqlite3.Connection, ledger_id: int, flow_type: str, source: str):
    """映射解析（v6 外键版）：source → 分类行 {id, name, type}，未命中返回 None。
    词典优先后无需区分 learned（学习映射不再需要内联防误判），外键保证目标必存在。
    带出 type：目标是「退款/不计收支」组时整行转不计收支。"""
    if not source:
        return None
    r = conn.execute(
        "SELECT m.category_id, c.name AS name, c.type AS type FROM category_mappings m "
        "JOIN categories c ON m.category_id=c.id "
        "WHERE m.ledger_id=? AND (m.flow_type=? OR c.type='transfer') AND m.source=?",
        (ledger_id, flow_type, source)).fetchone()
    return {"id": r["category_id"], "name": r["name"], "type": r["type"]} if r else None

def _purge_bad_learned_mappings(conn: sqlite3.Connection) -> int:
    """修复历史 AI 误判写入的脏学习映射（v6 单行模型）：
    ① 与内置默认同源但分类不同的学习行 → 删除后按默认补种；
    ② 与默认完全同源同分类的学习行 → 还原 learned=0（它就是默认）；
    ③ 补种缺失的默认映射。"""
    removed = 0
    with conn:
        for lid in [r["id"] for r in conn.execute("SELECT id FROM ledgers")]:
            for flow, source, target in dbm.DEFAULT_MAPPINGS:
                cid = dbm.top_cat_id(conn, lid, target, flow)
                if not cid:
                    continue
                row = conn.execute(
                    "SELECT id, category_id, learned FROM category_mappings "
                    "WHERE ledger_id=? AND flow_type=? AND source=?", (lid, flow, source)).fetchone()
                if row and row["learned"]:
                    if row["category_id"] != cid:
                        conn.execute("DELETE FROM category_mappings WHERE id=?", (row["id"],))
                        removed += 1
                        row = None
                    else:
                        conn.execute("UPDATE category_mappings SET learned=0 WHERE id=?", (row["id"],))
                        continue
                if not row:
                    conn.execute(
                        "INSERT OR IGNORE INTO category_mappings(ledger_id, flow_type, source, category_id) "
                        "VALUES(?,?,?,?)", (lid, flow, source, cid))
    return removed

def auto_fix_transactions(conn: sqlite3.Connection, ledger_id: int) -> int:
    """修复历史误分类流水：行文本（名称+商户+备注）命中关键词词典、
    但当前一级分类与词典矛盾（如停车费被误分为餐饮美食）→ 纠正到词典分类。
    仅按确定性关键词修，不碰转账/退款行。"""
    rows = conn.execute(
        "SELECT t.id, t.type, t.amount, t.name, t.merchant, t.note, p.name AS top_name "
        "FROM transactions t JOIN categories c ON t.category_id=c.id "
        "LEFT JOIN categories p ON c.parent_id=p.id "
        "WHERE t.ledger_id=? AND t.type IN ('expense','income') AND t.refund_for IS NULL",
        (ledger_id,)).fetchall()
    fixes = {}
    for r in rows:
        kw = _top_by_keyword(f"{r['name'] or ''} {r['merchant'] or ''} {r['note'] or ''}", r["amount"])
        if kw and kw != r["top_name"]:
            fixes.setdefault((kw, r["type"]), []).append(r["id"])
    n = 0
    with conn:
        for (top_name, ftype), ids in fixes.items():
            cat = conn.execute(
                "SELECT id FROM categories "
                "WHERE ledger_id=? AND parent_id IS NULL AND name=? AND type=?",
                (ledger_id, top_name, ftype)).fetchone()
            if not cat:
                continue
            for part in _chunks(ids):
                q = ",".join("?" * len(part))
                cur = conn.execute(
                    f"UPDATE transactions SET category_id=? WHERE id IN ({q})", (cat["id"], *part))
                n += cur.rowcount
    return n


# ---------- 分类瀑布（IMPORT_REFACTOR_PLAN §三；自 build_previews 显式化，行为逐字段等价） ----------

class ClassifyCtx:
    """分类瀑布的账本上下文（每次导入构建一次）：一级分类索引 + transfer 专用分类。"""

    def __init__(self, conn: sqlite3.Connection, ledger_id: int):
        self.conn, self.ledger_id = conn, ledger_id
        cats = list(conn.execute(
            "SELECT c.id, c.name, c.type, c.parent_id, p.name AS pname FROM categories c "
            "LEFT JOIN categories p ON c.parent_id=p.id WHERE c.ledger_id=?", (ledger_id,)))
        self.cat_top = {(c["name"], c["type"]): c for c in cats if not c["pname"]}
        self.tcat = {"退款": dbm.top_cat_id(conn, ledger_id, "退款", "transfer"),
                     "不计收支": dbm.top_cat_id(conn, ledger_id, "不计收支", "transfer")}

    def top(self, name: str, ftype: str):
        """按 (名字, 类型) 精确查一级分类——同名收支分类（人情往来）互不覆盖。"""
        return self.cat_top.get((name, ftype)) or self.cat_top.get((name, ""))

    def fallback(self, ftype: str):
        """兜底一级分类：支出→「其他」，收入→「其他收入」。"""
        return self.top("其他" if ftype == "expense" else "其他收入", ftype)


def classify_transfer(f: dict, ctx: ClassifyCtx, p: dict, export_tgt_amount=None) -> None:
    """转账行：子类映射（TRANSFER_SUBCLASS；未知子类 → 待确认）+ 导出回环退款对直连
    （负序号走 apply_import 回填，高置信免确认）。就 p 原地更新。"""
    sub = dbm.TRANSFER_SUBCLASS.get(f.get("source_category", ""), "账户转账")
    if f.get("is_refund") or f.get("export_refund_idx") is not None:
        sub = "退款"
    p["tags"]["transfer_sub"] = sub
    p["category_name"] = sub
    p["classified_via"] = "transfer"
    p["category_id"] = ctx.tcat["退款" if sub == "退款" else "不计收支"]
    if (not f.get("is_refund") and f.get("export_refund_idx") is None
            and f.get("source_category") not in dbm.TRANSFER_SUBCLASS
            and f.get("platform") != "export"):   # 导出回环的转账源端已确认，不重标
        p["need_confirm"] = True
    if f.get("export_refund_idx") is not None:
        p["refund_for"] = f["export_refund_idx"]
        p["refund_confidence"] = "high"
        p["refund_status"] = "full" if export_tgt_amount == f["amount"] else "partial"
        p["need_confirm"] = False


def classify_flow(f: dict, ctx: ClassifyCtx, p: dict) -> None:
    """支出/收入行五级链：①词典（人情二切）→ ②映射（含映射到退款/不计收支组 → 整行转
    transfer）→ ②' 同名直连 → ③AI 兜底（上层 ai 模块在预览后补）→ ④硬兜底+待确认；
    尾部人情复核。就 p 原地更新。"""
    # 来源就叫「人情往来」（导出格式）时尊重原分类，跳过词典的人情重切
    text = f"{f.get('name','')} {f.get('merchant','')} {f.get('note','')}"
    raw_cat = (f.get("source_category") or "").strip()
    src_is_renqing = raw_cat == "人情往来" and bool(ctx.top(raw_cat, f["type"]))
    # （组合支付备注里的「&红包」字样不该把已确认的电费改判成红包礼金）
    export_direct = (f.get("platform") == "export" and raw_cat
                     and ctx.top(raw_cat, f["type"]))
    kw = None if (src_is_renqing or export_direct) else _top_by_keyword(text, f["amount"])
    kw_top = ctx.top(kw, f["type"]) if kw else None
    if kw_top:
        top = kw_top
        p["classified_via"] = "keyword"
        if top["name"] == "红包礼金":
            p["need_confirm"] = dbm.renqing_need_confirm(text)
    else:
        # ② 映射表（v6 外键直取分类行，结构上无悬空）
        target = _resolve_by_mapping(ctx.conn, ctx.ledger_id, f["type"], raw_cat)
        direct = export_direct or (ctx.top(raw_cat, f["type"]) if raw_cat else None)
        if target and target["type"] == "transfer":
            # 用户显式指定 → 高置信不进待确认；top 设好后走公共尾行（人情复核天然跳过）
            sub = "退款" if target["name"] == "退款" else dbm.TRANSFER_SUBCLASS.get(raw_cat, "账户转账")
            p["type"] = "transfer"
            p["tags"]["transfer_sub"] = sub
            p["need_confirm"] = False
            p["classified_via"] = "mapping"
            top = {"id": target["id"], "name": sub}
        elif export_direct:
            top, p["classified_via"] = export_direct, "direct-name"
        elif target:
            top = target
            p["classified_via"] = "mapping"
        # ②' 同名直连：来源分类名与账本现有分类同名（本应用导出的分类名即同款）→ 直接归类
        elif direct:
            top = direct
            p["classified_via"] = "direct-name"
        else:
            top = ctx.fallback(f["type"])
            p["classified_via"] = "raw"
            p["need_confirm"] = True
    p["category_id"] = top["id"] if top else None
    p["parent_category"] = top["name"] if top else (
        raw_cat or ("其他" if f["type"] == "expense" else "其他收入"))
    # 记录来源分类名：用户在预览里纠正分类后回写学习映射的匹配键
    if raw_cat:
        p["map_source"] = raw_cat
    p["category_name"] = p["parent_category"]
    _renqing_review(f, ctx, p, text, raw_cat)


def _renqing_review(f: dict, ctx: ClassifyCtx, p: dict, text: str, raw_cat: str) -> None:
    """人情类复核：词典分支已按金额切分过，这里只复核映射/同名直连的结果；
    切分结果是「人情往来」直接入库，是「红包礼金」按统一口径决定是否待确认。"""
    if (p["classified_via"] != "keyword" and raw_cat != "人情往来"
            and f.get("platform") != "export"
            and p["parent_category"] in ("人情往来", "红包礼金")):
        alt = ctx.top(_renqing_split(f"{raw_cat} {text}", f["amount"]), f["type"])
        if alt:
            p["category_id"] = alt["id"]
            p["parent_category"] = p["category_name"] = alt["name"]
        p["need_confirm"] = (p["parent_category"] == "红包礼金"
                             and dbm.renqing_need_confirm(text))
