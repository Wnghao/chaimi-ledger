"""账单解析器冒烟：四平台解析、hash 口径、账户匹配（import_bill 规则密集区，回归主战场）。"""
import sys
import unittest
from base import EnvCase, SRC

sys.path.insert(0, str(SRC))
import import_bill as ib


class TestAlipay(EnvCase):
    ALI = ("交易时间,交易分类,交易对方,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注\n"
           "2026-09-01 08:30:00,餐饮美食,肯德基,肯德基早餐,支出,25.50,余额宝,交易成功,2026090101,BO1,\n"
           "2026-09-02 10:00:00,退款,某商户,退货退款,收入,99.00,账户余额,退款成功,2026090102,BO2,退款备注\n"
           "2026-09-03 09:00:00,退款,医保,医保支付,不计收支,0.00,账户余额,交易成功,2026090103,BO3,\n")

    def _parse(self):
        return ib.parse_bill(self.ALI.encode("utf-8"), "支付宝账单.csv")

    def test_expense_and_refund(self):
        flows = self._parse()
        f0, f1 = flows[0], flows[1]
        self.assertEqual(f0["type"], "expense")
        self.assertEqual(f0["amount"], 2550)
        self.assertEqual(f1["type"], "transfer")          # 退款强制 transfer
        self.assertTrue(f1["is_refund"])
        self.assertEqual(f1["amount"], 9900)
        self.assertEqual(f1["note"], "某商户 退款备注")     # 备注列回退最后取到

    def test_zero_amount_skipped(self):
        flows = self._parse()
        self.assertEqual(len(flows), 3)                   # 0 元行产出 skip 标记行
        self.assertEqual(flows[2]["skip"], "0 元退款行")
        self.assertTrue(all("hash_fields" in f for f in flows[:2]))

    def test_hash_amount_normalized_to_cents(self):
        f0 = self._parse()[0]
        keys = ["交易时间", "交易分类", "交易对方", "商品说明", "金额", "商家订单号", "备注"]
        self.assertEqual(f0["hash_fields"][keys.index("金额")], "2550")


class TestJD(EnvCase):
    """京东新导出（唯一模板）：括号只取原消费 + bracket_refund；其他括号待确认。"""
    JD = ("导出信息：\n京东账号名：测试\n\n"
          "交易时间,商户名称,交易说明,金额,收/付款方式,交易状态,收/支,交易分类,交易订单号,商家订单号,备注\n"
          "2026-09-03 12:00:00,京东自营,商品A,100(已退款30),京东支付,交易成功,支出,数码电器,JD1,B1,\n"
          "2026-09-04 12:00:00,京东自营,商品B,50(已退积分),京东支付,交易成功,支出,日用百货,JD2,B2,\n")

    def _parse(self):
        return ib.parse_bill(self.JD.encode("utf-8"), "京东账单.csv")

    def test_bracket_origin_with_refund_hint(self):
        fj = self._parse()
        origin = fj[0]
        self.assertEqual((origin["type"], origin["amount"]), ("expense", 10000))
        self.assertEqual(origin["bracket_refund"], 3000)  # 括号额：②'' 层关联线索
        self.assertEqual(origin["hash_fields"][4], "10000")
        self.assertFalse(origin.get("is_refund"))         # 不拆退款行（退款独立成行）

    def test_non_refund_paren_marks_confirm(self):
        fj = self._parse()
        paren = fj[1]                                     # 第二条是括号待确认行
        self.assertTrue(paren.get("need_confirm"))
        self.assertFalse(paren.get("is_refund"))


class TestAppExport(EnvCase):
    EX = ("交易时间,交易名称,交易金额,流水类型,支付账户,收款账户,支出类型/收入类型,备注,订单号,来源,流水ID,退款关联,流水归属\n"
          "2026-09-05 09:00:00,午餐,30.00,支出,现金,,餐饮美食,,E1,app,T1,,小明\n"
          "2026-09-05 12:00:00,退货,30.00,不计收支,现金,,退款,,E2,app,T2,T1,小明\n")

    def test_refund_roundtrip_by_id(self):
        fe = ib.parse_bill(self.EX.encode("utf-8"), "chaimi-ledger-导出.csv")
        self.assertEqual(fe[0]["export_id"], "T1")
        self.assertEqual(fe[1]["export_refund_idx"], -1)   # 批内直连负序号协议
        self.assertEqual(fe[0]["hash_fields"][2], "3000")  # 金额列规范化


class TestWechatHelpers(unittest.TestCase):
    def test_transfer_types_synced_with_db(self):
        """微信模板 transfer 集合（字面列表）须与 db.TRANSFER_SUBCLASS 派生集一致——
        db.py 演进（增删子类）而模板未跟随时在此报警（计划书 R4）。"""
        import db as dbm
        import platform_templates as pt
        derived = (set(dbm.TRANSFER_SUBCLASS)
                   - {"转账", "转账红包", "微信红包", "微信红包（单发）", "退款"})
        self.assertEqual(set(pt.WX_TRANSFER_TYPES), derived)

    def test_wx_name_placeholder(self):
        self.assertEqual(ib.wx_name("/", "张三"), "张三")     # 占位商品 → 对方顶替
        self.assertEqual(ib.wx_name("微信红包", "微信红包"), "")  # 双泛化 → 留空走 AI 兜底
        self.assertEqual(ib.wx_name("美团外卖", ""), "美团外卖")

    def test_is_meaningless_name(self):
        self.assertTrue(ib.is_meaningless_name("/"))
        self.assertTrue(ib.is_meaningless_name("微信转账"))
        self.assertFalse(ib.is_meaningless_name("滴滴出行"))

    def test_ttype_matrix(self):
        self.assertEqual(ib._ttype("支出", False), "expense")
        self.assertEqual(ib._ttype("收入", False), "income")
        self.assertEqual(ib._ttype("不计收支", False), "transfer")
        self.assertEqual(ib._ttype("收入", True), "transfer")   # 退款恒 transfer


class TestHashAndAccount(EnvCase):
    def test_hash_fields_cents_override(self):
        g = lambda k: {"金额": "100(已退款30)"}.get(k, "")
        out = ib._hash_fields(g, ["交易时间", "金额"], "金额", 10000)
        self.assertEqual(out[1], "10000")

    def test_match_account(self):
        c = self.conn
        bank = ib._match_account(c, self.lid, "alipay", "招商银行储蓄卡(7529)")
        self.assertIsInstance(bank, int)                       # 银行卡按原文自动建户
        wallet = ib._match_account(c, self.lid, "wechat", "零钱")
        self.assertIsInstance(wallet, int)                     # 平台钱包 → 平台账户
        self.assertIsNone(ib._match_account(c, self.lid, "export", "不存在的怪账户"))


class TestRowCap(unittest.TestCase):
    """解析期行数熔断（复查项②）：超限在迭代中抛出，不再全量物化。"""

    def test_capped_passes_within_limit(self):
        self.assertEqual(len(list(ib._capped(range(100)))), 100)

    def test_capped_raises_over_limit(self):
        with self.assertRaises(ValueError):
            list(ib._capped(range(ib.MAX_PARSE_ROWS + 500)))

    def test_alipay_overload_stopped_in_parser(self):
        head = "交易时间,交易分类,交易对方,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注\n"
        body = "2026-09-01 08:30:00,餐饮美食,肯德基,早餐,支出,25.50,余额宝,交易成功,1,1,\n"
        with self.assertRaises(ValueError):
            ib.parse_bill((head + body * (ib.MAX_PARSE_ROWS + 500)).encode("utf-8"), "支付宝.csv")


class TestRefundMatch(EnvCase):
    """退款分层匹配（A 收紧后）：高置信需 唯一+窗口+长度，模糊场景降 low 人工裁决。"""

    def _expense(self, name, amount, date, order_no=None, source="app"):
        return self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=amount,
                                  date=date, name=name, order_no=order_no, source=source)

    def _run(self, name, amount=3000, date="2026-09-10 12:00:00", order_no="", platform="alipay", **flow_kw):
        p = {"type": "transfer", "amount": amount, "date": date, "name": name,
             "order_no": order_no, "need_confirm": False, "merchant": ""}
        f = {"is_refund": True, "platform": platform}
        f.update(flow_kw)
        ib._match_refunds(self.conn, self.lid, [p], [f])
        return p

    def test_order_no_high(self):
        eid = self._expense("任意品名", 3000, "2026-09-05 10:00:00", order_no="X1")
        p = self._run("退款", order_no="X1")
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (eid, "high"))
        self.assertFalse(p["need_confirm"])

    def test_order_prefers_equal_amount_among_same_order(self):
        """同单号多行（平台退款行带原单号/商家单号拆单共用）：优先等额那行——
        真机案例：35 退款与 100/35 两行同单号，旧①层直接链第一条（100）。"""
        self._expense("拆单商品甲", 10000, "2026-10-08 21:12:45", order_no="S1")
        eq = self._expense("拆单商品乙", 3500, "2026-10-08 21:12:24", order_no="S1")
        p = self._run("退款-拆单商品乙", amount=3500, date="2026-10-09 08:53:26", order_no="S1")
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (eq, "high"))

    def test_order_unique_partial_refund_still_high(self):
        """同单号唯一时不校验金额——同订单部分退款（100 退 35）保持高置信直连。"""
        eid = self._expense("大件", 10000, "2026-10-08 21:12:45", order_no="S1")
        p = self._run("退款-大件", amount=3500, date="2026-10-09 08:53:26", order_no="S1")
        self.assertEqual((p["refund_for"], p["refund_confidence"], p["refund_status"]),
                         (eid, "high", "partial"))

    def test_order_ambiguous_defers_to_amount_layers(self):
        """同单号多行且无等额：单号证据不足以定行，让位金额证据（③ 同额唯一 → low）。"""
        self._expense("甲", 10000, "2026-10-08 21:12:45", order_no="S1")
        self._expense("乙", 8000, "2026-10-08 21:12:24", order_no="S1")
        eq = self._expense("另一单的等额商品", 3500, "2026-10-08 20:00:00", order_no="S2")
        p = self._run("退款-无关名", amount=3500, date="2026-10-09 08:53:26", order_no="S1")
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (eq, "low"))

    def test_name_unique_in_window_high(self):
        eid = self._expense("纯牛奶250ml", 3000, "2026-09-05 10:00:00")
        p = self._run("纯牛奶")
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (eid, "high"))

    def test_name_multi_hits_demote_low(self):
        self._expense("牛奶糖", 3000, "2026-09-01 10:00:00")
        near = self._expense("牛奶饼干", 3000, "2026-09-05 10:00:00")
        p = self._run("牛奶")
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (near, "low"))
        self.assertTrue(p["need_confirm"])            # 多候选：取最近但降级，人工裁决

    def test_name_stale_demoted_low(self):
        self._expense("远期牛奶", 3000, "2026-01-01 10:00:00")   # 距退款日 >180 天
        p = self._run("远期牛奶")
        self.assertEqual(p["refund_confidence"], "low")          # 旧版此处直接 high
        self.assertTrue(p["need_confirm"])

    def test_short_prefix_not_high(self):
        self._expense("书写工具礼盒", 5000, "2026-09-08 10:00:00")
        p = self._run("书", amount=5000)
        self.assertEqual(p["refund_confidence"], "low")          # 单字前缀不作 high 证据

    def test_wechat_packet_window(self):
        self._expense("某红包", 1000, "2026-08-01 10:00:00", source="wechat")   # 窗外 40 天
        near = self._expense("又一红包", 1000, "2026-09-09 10:00:00", source="wechat")
        p = self._run("微信红包", amount=1000, platform="wechat", wechat_type="微信红包")
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (near, "low"))

    def test_wechat_packet_prefers_same_counterparty(self):
        """红包/转账退款同额多候选：优先交易对方相同的那笔（退给谁=原转账给谁）——
        真机案例：35 转账退款被链到更近的别人的 35 转账。"""
        other = self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=3500,
                                   date="2026-08-24 20:00:00", name="发给李星军",
                                   merchant="李星军", source="wechat")
        mine = self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=3500,
                                  date="2026-08-24 19:00:00", name="发给明玉",
                                  merchant="明玉", source="wechat")
        p = {"type": "transfer", "amount": 3500, "date": "2026-08-25 12:15:00", "name": "",
             "merchant": "明玉", "order_no": "", "need_confirm": False}
        f = {"is_refund": True, "platform": "wechat", "wechat_type": "转账"}
        ib._match_refunds(self.conn, self.lid, [p], [f])
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (mine, "low"))
        self.assertNotEqual(p["refund_for"], other)

    def test_unmatched_goes_queue(self):
        self._expense("无关小额", 100, "2026-09-01 10:00:00")
        p = self._run("完全无关的名字", amount=777)
        self.assertEqual((p["refund_confidence"], p.get("refund_for")), ("unmatched", None))
        self.assertTrue(p["need_confirm"])

    def test_heuristic_unique_low(self):
        eid = self._expense("大件商品", 2500, "2026-09-01 10:00:00")
        self._expense("小额商品", 100, "2026-09-02 10:00:00")
        p = self._run("任意名称", amount=2000)
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (eid, "low"))

    def test_fuzzy_prefers_equal_amount(self):
        """真机案例回归（2026-10-09）：9.9 退款与同店 12.9/9.9 两笔模糊相似度打平、
        12.9 时间更晚——同额优先于相似度与新旧，链 9.9 那笔（low 仍进待确认）。"""
        self._expense("多多优卖生鲜卖场（中山路店）", 1290, "2026-10-08 21:12:45")
        eq = self._expense("多多优卖生鲜卖场（中山路店）", 990, "2026-10-08 21:12:24")
        p = self._run("多多优卖生鲜卖场中山路店", amount=990, date="2026-10-09 08:53:26")
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (eq, "low"))
        self.assertTrue(p["need_confirm"])

    def test_heuristic_prefers_equal_amount(self):
        """③ 层：多候选（12.9/9.9）但恰有一笔同额 → 优先同额（low，仍待确认人工把关）。"""
        self._expense("甲商品", 1290, "2026-10-08 21:12:45")
        eq = self._expense("乙商品", 990, "2026-10-08 21:12:24")
        p = self._run("完全无关的退款名", amount=990, date="2026-10-09 08:53:26")
        self.assertEqual((p["refund_for"], p["refund_confidence"]), (eq, "low"))

    def test_heuristic_equal_amount_ambiguous_still_unmatched(self):
        """同额也有两笔（歧义）→ 放弃 unmatched——同额优先不破坏宁低勿错。"""
        self._expense("甲商品", 990, "2026-10-08 21:12:24")
        self._expense("乙商品", 990, "2026-10-08 21:12:45")
        p = self._run("完全无关的退款名", amount=990, date="2026-10-09 08:53:26")
        self.assertEqual(p["refund_confidence"], "unmatched")


class TestRescanRefunds(EnvCase):
    """rescan 与主匹配同口径：金额约束 + 窗口 + 低置信标记（M2 静默错链回归锁定）。"""

    def _refund_row(self, name, amount, date, order_no=None):
        return self.dbm.insert_tx(self.conn, self.lid, type="transfer", amount=amount,
                                  date=date, name=name, order_no=order_no,
                                  tags={"transfer_sub": "退款"})

    def test_rescan_links_equal_amount_as_low(self):
        rid = self._refund_row("退款-纯牛奶", 3000, "2026-09-10 10:00:00")
        eid = self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=3000,
                                 date="2026-09-05 10:00:00", name="纯牛奶一箱")
        ib._rescan_unmatched_refunds(self.conn, self.lid)
        row = self.conn.execute("SELECT refund_for, refund_confidence FROM transactions WHERE id=?",
                                (rid,)).fetchone()
        self.assertEqual((row["refund_for"], row["refund_confidence"]), (eid, "low"))

    def test_rescan_rejects_amount_mismatch(self):
        """M2 回归：旧版名称前缀命中即链（金额只算 full/partial 不筛候选），30 元退款曾链上 300 元消费。"""
        rid = self._refund_row("退款-牛奶", 3000, "2026-09-10 10:00:00")
        self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=30000,
                           date="2026-09-05 10:00:00", name="牛奶箱装整箱")
        ib._rescan_unmatched_refunds(self.conn, self.lid)
        row = self.conn.execute("SELECT refund_for FROM transactions WHERE id=?", (rid,)).fetchone()
        self.assertIsNone(row["refund_for"])

    def test_rescan_multi_candidate_picks_most_recent(self):
        """名称层多候选取最近一条：快照池日期倒序显式定序（旧 SQL 分支无 ORDER BY，取哪条随机）。"""
        rid = self._refund_row("退款-牛奶", 3000, "2026-09-10 10:00:00")
        self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=3000,
                           date="2026-09-01 10:00:00", name="牛奶箱装")
        newer = self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=3000,
                                   date="2026-09-08 10:00:00", name="牛奶盒装")
        ib._rescan_unmatched_refunds(self.conn, self.lid)
        row = self.conn.execute("SELECT refund_for FROM transactions WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["refund_for"], newer)

    def test_rescan_strong_name_no_placeholder_fallback(self):
        """名称候选有效（strong）但无命中 → 放弃，不退占位分支（等额+30 天窗那套不启用）。"""
        rid = self._refund_row("退款-牛奶", 3000, "2026-09-10 10:00:00")
        self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=3000,
                           date="2026-09-09 10:00:00", name="完全无关的名字")
        ib._rescan_unmatched_refunds(self.conn, self.lid)
        row = self.conn.execute("SELECT refund_for FROM transactions WHERE id=?", (rid,)).fetchone()
        self.assertIsNone(row["refund_for"])


class TestWechatXlsxDispatch(unittest.TestCase):
    def test_long_preamble_dispatch(self):
        """微信导出 xlsx 的前言块（昵称/起止时间/共N笔…）可达 16+ 行：
        表头嗅探窗口须覆盖——曾用 max_row=12 漏判新导出格式（真机 2026-10-13 复现）。"""
        from openpyxl import Workbook
        import io
        wb = Workbook(); ws = wb.active
        for i in range(16):
            ws.append([f"微信导出信息行{i + 1}：xxxx"])
        ws.append([])
        ws.append(["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
                   "支付方式", "当前状态", "交易单号", "商户单号"])
        ws.append(["2026-09-01 08:30:00", "商户消费", "肯德基", "早餐", "支出",
                   "25.50", "零钱", "支付成功", "WX1", "S1"])
        buf = io.BytesIO(); wb.save(buf)
        flows = ib.parse_bill(buf.getvalue(), "微信支付账单(202609).xlsx")
        self.assertEqual(len(flows), 1)
        self.assertEqual((flows[0]["type"], flows[0]["amount"]), ("expense", 2550))


class TestLexiconPlatformSafety(unittest.TestCase):
    """电商平台名不进词典（平台上什么都买，固定归类必误伤——词典"只收高精度词"纪律）。
    平台商户消费的正确路径：词典不命中 → classified_via=raw → AI 兜底读商品文本分类
    （配 Key 时）/ 其他+待确认（未配 Key）。本用例锁定"不误命中"这一前提——
    防止将来扩词典时（如给文化休闲加"书"字词）平台名被殃及。"""

    def test_platform_names_not_hit(self):
        for plat in ("小红书", "小红书福利社", "抖音商城", "抖音支付",
                     "拼多多", "美团平台", "天猫", "淘宝", "京东购物"):
            self.assertIsNone(ib._top_by_keyword(plat, 5000), f"{plat} 不应误命中词典")


class TestMeituanGeneric(EnvCase):
    """美团官方导出（2026-10-13 真机样本结构脱敏）：通用模板预设 + 表头集合自动分流。"""

    CSV = (
        "美团交易账单明细\n"
        "美团用户名：[测试用户]\n"
        "起始时间：[2026-07-13] 终止时间：[2026-10-13]\n"
        "导出交易类型：[全部]\n"
        "导出时间：[2026-10-13 21:13:49]\n"
        '""\n'
        "共：2笔记录\n"
        "支出：1笔 22.46元\n"
        "收入：1笔 0.03元\n"
        "不计收支：0笔 0.00元\n"
        '""\n'
        "特别提示：\n"
        "1.本明细仅限个人对账使用。\n"
        "2.如有疑问请联系客服。\n"
        "【美团交易账单明细列表】\n"
        "交易创建时间,交易成功时间,交易类型,订单标题,收/支,支付方式,订单金额,实付金额,交易单号,商家单号,备注\n"
        "2026-09-29 21:15:27,2026-09-29 21:15:28,退款,小象超市（城东大道店） 订单详情,收入,"
        "中国农业银行储蓄卡(3878),¥0.03,¥0.03,\"2609290030354160951\t\",\"1_79064664339\t\",/\n"
        "2026-09-29 20:58:05,2026-09-29 20:58:25,支付,小象超市（城东大道店） 订单详情,支出,"
        "中国农业银行储蓄卡(3878),¥22.46,¥22.46,\"26092911200701670004695370095951\t\",\"1_2330263053779088\t\",/\n"
    )

    def test_parse_and_autodispatch(self):
        data = self.CSV.encode("utf-8-sig")            # 真机为 UTF-8 BOM
        flows = ib.parse_bill(data, "美团账单(20260713-20261013).csv")
        self.assertEqual(len(flows), 2)
        f0, f1 = flows[0], flows[1]
        self.assertEqual((f0["type"], f0["is_refund"], f0["amount"]), ("transfer", True, 3))   # 退款独立行
        self.assertEqual((f1["type"], f1["amount"]), ("expense", 2246))
        self.assertEqual(f1["platform"], "meituan")
        self.assertEqual(f1["name"], "小象超市（城东大道店）")        # "订单详情"后缀已清理
        self.assertEqual(f1["attribution"], "测试用户")               # 前言提取账单主人
        self.assertEqual(f1["order_no"], "26092911200701670004695370095951")  # 尾部制表符已剥
        self.assertEqual(f1["pay_account"], "中国农业银行储蓄卡(3878)")
        self.assertEqual(f1["note"], "")                              # 备注占位 "/" 滤除

    def test_build_previews_bank_account_and_refund(self):
        flows = ib.parse_bill(self.CSV.encode("utf-8-sig"), "m.csv")
        pv = ib.build_previews(self.conn, self.lid, flows)
        pay = [p for p in pv if p["type"] == "expense"][0]
        self.assertTrue(pay["account_name"].startswith("中国农业银行"))  # 银行卡自动建户
        refund = [p for p in pv if p["type"] == "transfer"][0]
        self.assertEqual((refund["refund_confidence"], refund["need_confirm"]), ("low", True))
        self.assertLess(refund["refund_for"], 0)                      # 批内负 id 关联到支付预览


class TestGenericWizard(EnvCase):
    """向导链路：嗅探定位/映射猜测、用户模板（字面归属）、保存到 conf 后自动分流。"""

    CSV = ("某某平台账单\n"
           "用户：[张三]\n"
           "日期,描述,金额(元),方向,备注\n"
           "2026-09-01,午饭,25.5,支出,\n"
           "2026-09-02,发工资,8000,收入,九月工资\n"
           "2026-09-03,退款-午饭,25.5,收入,\n")
    TPL = {"id": "custom", "name": "某某平台",
           "headers": ["日期", "描述", "金额(元)", "方向", "备注"],
           "col": {"date": "日期", "name": "描述", "amount": "金额(元)", "flow": "方向", "note": "备注"},
           "flow_map": {"支出": "expense", "收入": "income"},
           "refund_type": "", "name_strip": [], "owner": "张三"}

    def test_sniff_headers_guess_samples(self):
        s = ib.sniff_template(self.CSV)
        self.assertEqual(s["header_row"], 2)                       # 跳过两行前言
        self.assertEqual(s["guess"]["date"], "日期")
        self.assertEqual(s["guess"]["amount"], "金额(元)")
        self.assertEqual(s["guess"]["name"], "描述")
        self.assertEqual(s["guess"]["flow"], "方向")
        self.assertEqual(len(s["samples"]), 3)

    def test_user_template_literal_owner(self):
        flows = ib.parse_generic(self.CSV, self.TPL)
        self.assertEqual(len(flows), 3)
        self.assertEqual([f["type"] for f in flows], ["expense", "income", "income"])
        self.assertEqual(flows[0]["amount"], 2550)
        self.assertTrue(all(f["attribution"] == "张三" for f in flows))   # 字面归属（无正则）
        self.assertEqual(flows[2]["hash_fields"][2], "2550")             # 金额归一化参与身份

    def test_saved_template_autodispatch(self):
        import config
        cfg = config.load()
        cfg["import_templates"] = [{**self.TPL, "owner": ""}]
        config.save(cfg)
        flows = ib.parse_bill(self.CSV.encode("utf-8"), "任意文件名.csv")   # 内置识别不中 → 模板分流
        self.assertEqual(len(flows), 3)
        self.assertTrue(all(f["platform"] == "custom" for f in flows))

    def test_meituan_preset_still_first(self):
        """预设模板与用户模板共存：美团文件仍走预设（分流顺序无回归）。"""
        flows = ib.parse_bill(TestMeituanGeneric.CSV.encode("utf-8-sig"), "m.csv")
        self.assertTrue(all(f["platform"] == "meituan" for f in flows))


class TestLinkPayment(EnvCase):
    """P2 跨源配对：平台账单（美团等）的支付行 vs 库中支付渠道（微信/支付宝/京东）流水。
    同一笔消费两边各出现 → 后导入的标 pay_dupe + skip（可恢复）。"""

    def _seed_wechat_tx(self, name, merchant, amount, date, order_no="", source="wechat"):
        return self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=amount,
                                  date=date, name=name, merchant=merchant,
                                  order_no=order_no, source=source)

    def _mk_meituan_preview(self, name="小象超市", amount=2246, date="2026-09-29 20:58:25",
                            order_no="26092911200701670004695370095951"):
        return {"type": "expense", "amount": amount, "date": date, "name": name,
                "merchant": "", "note": "", "platform": "meituan", "source": "meituan",
                "order_no": order_no, "row_hash": "h1", "category_id": None,
                "category_name": "", "parent_category": "", "classified_via": "raw",
                "need_confirm": False, "refund_for": None, "refund_status": "none",
                "refund_confidence": None, "duplicate": False, "duplicate_of": None,
                "to_account_id": None, "tags": {}, "ai_meta": {}, "skip": False,
                "pay_dupe": False, "pay_dupe_of": None, "pay_dupe_via": ""}

    def test_order_no_ambiguous_picks_earliest(self):
        """订单号多候选歧义（库中两行同订单号等额同窗）取最早一条：快照 id 升序显式定序，
        等价并固化旧 SQL 默认扫描序——结果从"碰巧"变为确定。"""
        first = self._seed_wechat_tx("某店", "某商户", 2246, "2026-09-29 20:58:00",
                                     order_no="DUP1", source="wechat")
        self._seed_wechat_tx("另一笔", "另一商户", 2246, "2026-09-29 21:00:00",
                             order_no="DUP1", source="alipay")
        pv = [self._mk_meituan_preview(order_no="DUP1")]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertTrue(pv[0]["pay_dupe"])
        self.assertEqual(pv[0]["pay_dupe_of"], first)

    def test_merchant_name_bridge(self):
        """同日±1 + 金额相等 + 库中行商户含平台名 → pay_dupe。"""
        self._seed_wechat_tx("美团", "美团平台", 2246, "2026-09-29 20:58:00", source="wechat")
        pv = [self._mk_meituan_preview()]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertTrue(pv[0]["pay_dupe"])
        self.assertEqual(pv[0]["pay_dupe_via"], "商户名")
        self.assertTrue(pv[0]["skip"])

    def test_order_no_bridge(self):
        """订单号跨源相等 + 金额相等 → pay_dupe（优先于商户名）。"""
        self._seed_wechat_tx("某店", "某商户", 2246, "2026-09-29 20:58:00",
                             order_no="26092911200701670004695370095951", source="alipay")
        pv = [self._mk_meituan_preview()]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertTrue(pv[0]["pay_dupe"])
        self.assertEqual(pv[0]["pay_dupe_via"], "订单号")

    def test_amount_mismatch_no_dupe(self):
        """金额不等不配对（改价/部分退）。"""
        self._seed_wechat_tx("美团", "美团平台", 3000, "2026-09-29 20:58:00")
        pv = [self._mk_meituan_preview(amount=2246)]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertFalse(pv[0]["pay_dupe"])

    def test_date_out_of_window_no_dupe(self):
        """日期差 > 1 天不配对。"""
        self._seed_wechat_tx("美团", "美团平台", 2246, "2026-10-05 20:58:00")
        pv = [self._mk_meituan_preview()]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertFalse(pv[0]["pay_dupe"])

    def test_same_source_no_dupe(self):
        """同来源（同为 meituan）不跨源配对。"""
        self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=2246,
                           date="2026-09-29 20:58:00", name="美团", source="meituan")
        pv = [self._mk_meituan_preview()]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertFalse(pv[0]["pay_dupe"])

    def test_no_match_at_all_no_dupe(self):
        """所有层都不命中（商户不匹配 + 订单号不等 + 日期差 > 1 天）→ 不配对。"""
        self._seed_wechat_tx("肯德基", "肯德基", 2246, "2026-10-15 20:58:00", order_no="OTHER")
        pv = [self._mk_meituan_preview()]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertFalse(pv[0]["pay_dupe"])

    def test_apply_import_skips_pay_dupe(self):
        """pay_dupe 行在 apply_import 中与 duplicate 同等跳过。"""
        self._seed_wechat_tx("美团", "美团平台", 2246, "2026-09-29 20:58:00")
        pv = [self._mk_meituan_preview()]
        ib._link_payment(self.conn, self.lid, pv)
        bid = ib.apply_import(self.conn, self.lid, pv, "美团.csv", "meituan", False)
        n = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE source='meituan'").fetchone()[0]
        self.assertEqual(n, 0)   # 跨源重复行未入库
        cnt = self.conn.execute("SELECT count FROM import_batches WHERE id=?", (bid,)).fetchone()[0]
        self.assertEqual(cnt, 0)

    def test_same_day_amount_cross_source_dupe(self):
        """③ 同日+金额+跨来源（无商户名线索）→ pay_dupe（须唯一候选）。"""
        self._seed_wechat_tx("某外卖", "肯德基", 2246, "2026-09-29 20:58:00", source="wechat")
        pv = [self._mk_meituan_preview()]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertTrue(pv[0]["pay_dupe"])
        self.assertEqual(pv[0]["pay_dupe_via"], "同日同额")

    def test_same_day_amount_ambiguous_no_dupe(self):
        """③ 多笔等额候选（歧义）→ 不自动跳过。"""
        self._seed_wechat_tx("某外卖", "肯德基", 2246, "2026-09-29 20:58:00", source="wechat")
        self._seed_wechat_tx("另一笔", "麦当劳", 2246, "2026-09-29 21:00:00", source="alipay")
        pv = [self._mk_meituan_preview()]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertFalse(pv[0]["pay_dupe"])

    def test_enrich_replaces_generic_name(self):
        """态A 增强：跨源重复时，已有流水的泛化名被替换为平台订单标题。"""
        wid = self._seed_wechat_tx("/", "美团平台", 2246, "2026-09-29 20:58:00")
        pv = [self._mk_meituan_preview(name="小象超市（城东大道店）", order_no="MT999")]
        ib._link_payment(self.conn, self.lid, pv)
        ib.apply_import(self.conn, self.lid, pv, "美团.csv", "meituan", False)
        row = self.conn.execute("SELECT name, order_no FROM transactions WHERE id=?", (wid,)).fetchone()
        self.assertEqual(row["name"], "小象超市（城东大道店）")   # 泛化名 → 订单标题
        self.assertEqual(row["order_no"], "MT999")                 # 订单号补充

    def test_enrich_preserves_manual_name(self):
        """增强不覆盖用户手改的名称——只有泛化值（占位/等于商户名）才替换。"""
        wid = self._seed_wechat_tx("我改过的名字", "美团平台", 2246, "2026-09-29 20:58:00")
        pv = [self._mk_meituan_preview(name="小象超市")]
        ib._link_payment(self.conn, self.lid, pv)
        ib.apply_import(self.conn, self.lid, pv, "美团.csv", "meituan", False)
        row = self.conn.execute("SELECT name FROM transactions WHERE id=?", (wid,)).fetchone()
        self.assertEqual(row["name"], "我改过的名字")   # 手改名不动

    def test_enrich_no_overwrite_when_name_equals_merchant(self):
        """名称恰好等于商户名（无信息量）→ 也替换为订单标题。"""
        wid = self._seed_wechat_tx("美团平台", "美团平台", 2246, "2026-09-29 20:58:00")
        pv = [self._mk_meituan_preview(name="小象超市")]
        ib._link_payment(self.conn, self.lid, pv)
        ib.apply_import(self.conn, self.lid, pv, "美团.csv", "meituan", False)
        row = self.conn.execute("SELECT name FROM transactions WHERE id=?", (wid,)).fetchone()
        self.assertEqual(row["name"], "小象超市")

    def test_enrich_no_duplicate_order_no(self):
        """已有订单号不覆盖。"""
        wid = self._seed_wechat_tx("/", "美团平台", 2246, "2026-09-29 20:58:00", order_no="WX_ORIG")
        pv = [self._mk_meituan_preview(order_no="MT999")]
        ib._link_payment(self.conn, self.lid, pv)
        ib.apply_import(self.conn, self.lid, pv, "美团.csv", "meituan", False)
        row = self.conn.execute("SELECT order_no FROM transactions WHERE id=?", (wid,)).fetchone()
        self.assertEqual(row["order_no"], "WX_ORIG")

    def test_reverse_wechat_after_meituan(self):
        """反向：先导美团（已入库），再导微信 → 微信行标 pay_dupe 跳过。"""
        self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=2246,
                           date="2026-09-29 20:58:00", name="小象超市（城东大道店）",
                           merchant="", source="meituan")
        wx = {"type": "expense", "amount": 2246, "date": "2026-09-29 20:58:00",
              "name": "美团平台", "merchant": "美团平台", "note": "", "platform": "wechat",
              "source": "wechat", "order_no": "WX123", "skip": False,
              "duplicate": False, "pay_dupe": False, "pay_dupe_of": None, "pay_dupe_via": ""}
        ib._link_payment(self.conn, self.lid, [wx])
        self.assertTrue(wx["pay_dupe"])
        self.assertEqual(wx["pay_dupe_via"], "跨源（反向）")
        self.assertTrue(wx["skip"])
        # 反向不增强（美团行已有丰富数据，无需回填）

    def test_bidirectional_no_duplication(self):
        """双向完整流程：先导美团 → 再导微信 → 只有一笔支出。"""
        from openpyxl import Workbook; import io
        MT = ("美团交易账单明细\n美团用户名：[测试]\n\n"
              "交易创建时间,交易成功时间,交易类型,订单标题,收/支,支付方式,订单金额,实付金额,交易单号,商家单号,备注\n"
              "2026-09-29 20:58:25,2026-09-29 20:58:25,支付,小象超市（城东大道店） 订单详情,支出,中国农业银行储蓄卡(3878),¥22.46,¥22.46,\"MT001\t\",\"1_1\t\",/\n")
        mt_flows = ib.parse_bill(MT.encode("utf-8-sig"), "美团.csv")
        mt_pv = ib.build_previews(self.conn, self.lid, mt_flows)
        ib.apply_import(self.conn, self.lid, mt_pv, "美团.csv", "meituan", True)

        wb = Workbook(); ws = wb.active
        for i in range(16): ws.append([f"前言{i}"])
        ws.append([]); ws.append(["交易时间","交易类型","交易对方","商品","收/支","金额(元)","支付方式","当前状态","交易单号","商户单号"])
        ws.append(["2026-09-29 20:58:25","商户消费","美团平台","/","支出","22.46","零钱","支付成功","WX123","M1"])
        buf = io.BytesIO(); wb.save(buf)
        wx_flows = ib.parse_bill(buf.getvalue(), "微信.xlsx")
        wx_pv = ib.build_previews(self.conn, self.lid, wx_flows)
        ib.apply_import(self.conn, self.lid, wx_pv, "微信.xlsx", "wechat", True)

        n = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE type='expense' AND amount=2246").fetchone()[0]
        self.assertEqual(n, 1, f"应为 1 条支出，实际 {n} 条")


class TestManualPairing(EnvCase):
    """手动记账 × 平台账单配对（_link_payment 末层）：先手记后导账单的双计兜底。
    默认跳过（预览点行恢复=用户确认）；唯一候选 + 1:1 消耗双防线防误吞同额真实消费。"""

    def _seed_manual(self, name, amount, date, type_="expense", order_no=""):
        return self.dbm.insert_tx(self.conn, self.lid, type=type_, amount=amount,
                                  date=date, name=name, source="app", order_no=order_no)

    def _wx_preview(self, amount, date, name="转账", order_no="WX1", type_="expense"):
        return {"type": type_, "amount": amount, "date": date, "name": name,
                "merchant": "", "note": "", "platform": "wechat", "source": "wechat",
                "order_no": order_no, "row_hash": "h1", "category_id": None,
                "category_name": "", "parent_category": "", "classified_via": "raw",
                "need_confirm": False, "refund_for": None, "refund_status": "none",
                "refund_confidence": None, "duplicate": False, "duplicate_of": None,
                "to_account_id": None, "tags": {}, "ai_meta": {}, "skip": False,
                "pay_dupe": False, "pay_dupe_of": None, "pay_dupe_via": ""}

    def test_manual_income_paired_skipped_and_enriched(self):
        """房租场景（真机 2026-10-10 双计反馈）：手记收入 2600 + 微信导入 2600 →
        配对跳过；手记行名称不动、订单号补上。"""
        mid = self._seed_manual("房租", 260000, "2026-10-10 07:34:00", "income")
        pv = [self._wx_preview(260000, "2026-10-10 07:33:00", order_no="WX2600", type_="income")]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertTrue(pv[0]["pay_dupe"])
        self.assertEqual(pv[0]["pay_dupe_via"], "手动记账")
        self.assertTrue(pv[0]["skip"])
        ib.apply_import(self.conn, self.lid, pv, "微信.csv", "wechat", False)
        n = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE source='wechat'").fetchone()[0]
        self.assertEqual(n, 0)                              # 配对行未入库
        row = self.conn.execute("SELECT name, order_no FROM transactions WHERE id=?", (mid,)).fetchone()
        self.assertEqual(row["name"], "房租")               # 手填名称不被覆盖
        self.assertEqual(row["order_no"], "WX2600")         # 订单号补充

    def test_manual_expense_paired(self):
        """月亮湾市集场景：手记支出 62.73 + 微信同额扫枪收款 → 配对。"""
        self._seed_manual("月亮湾市集", 6273, "2026-10-10 12:00:00")
        pv = [self._wx_preview(6273, "2026-10-10 12:05:00", name="月亮湾市集扫枪收款")]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertTrue(pv[0]["pay_dupe"])

    def test_manual_adjacent_day_window(self):
        """±1 天窗：手记日期差一天也能配上（人填日期常差一天）。"""
        self._seed_manual("房租", 260000, "2026-10-09 23:00:00", "income")
        pv = [self._wx_preview(260000, "2026-10-10 07:33:00", type_="income")]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertTrue(pv[0]["pay_dupe"])

    def test_manual_ambiguous_not_paired(self):
        """库中两笔同额手记（歧义）→ 不自动跳，留 dup_hint 人工裁决。"""
        self._seed_manual("房租", 260000, "2026-10-10 07:00:00", "income")
        self._seed_manual("另一笔", 260000, "2026-10-10 08:00:00", "income")
        pv = [self._wx_preview(260000, "2026-10-10 07:33:00", type_="income")]
        ib._link_payment(self.conn, self.lid, pv)
        self.assertFalse(pv[0]["pay_dupe"])

    def test_manual_one_absorbs_only_one(self):
        """1:1 消耗：手记 1 笔 5 元停车 + 账单 3 笔同额 → 只跳第一笔，余两笔正常入库。"""
        self._seed_manual("停车费", 500, "2026-10-18 09:00:00")
        pv = [self._wx_preview(500, "2026-10-18 09:01:00", name="城南停车场", order_no="P1"),
              self._wx_preview(500, "2026-10-18 12:30:00", name="城北停车场", order_no="P2"),
              self._wx_preview(500, "2026-10-18 18:05:00", name="江边停车场", order_no="P3")]
        for i, p in enumerate(pv):
            p["row_hash"] = f"h{i}"
        ib._link_payment(self.conn, self.lid, pv)
        self.assertTrue(pv[0]["pay_dupe"])
        self.assertFalse(pv[1]["pay_dupe"])
        self.assertFalse(pv[2]["pay_dupe"])

    def test_manual_three_vs_three_none_paired(self):
        """3 笔手记 vs 3 笔同额导入（无法分辨谁是重复）→ 全不跳（宁低勿错）。"""
        for i in range(3):
            self._seed_manual(f"停车费{i}", 500, f"2026-10-18 1{i}:00:00")
        pv = [self._wx_preview(500, f"2026-10-18 1{i}:05:00", order_no=f"P{i}") for i in range(3)]
        for i, p in enumerate(pv):
            p["row_hash"] = f"h{i}"
        ib._link_payment(self.conn, self.lid, pv)
        self.assertFalse(any(p["pay_dupe"] for p in pv))


class TestBatchRefundBackfill(EnvCase):
    """批内退款回填（_insert_rows 负序号落库）：退款对之前存在被跳过的行时
    按预览序号映射回填，不错位。"""

    CSV = ("微信昵称：测试\n"
           "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号\n"
           "2026-08-25 12:15:03,微信红包-退款,/,/,收入,35.00,零钱,已退款,REF1,M1\n"
           "2026-08-24 12:15:00,微信红包（单发）,发给李星军,/,支出,35.00,零钱,支付成功,ORI1,M2\n")

    def _import_once(self):
        flows = ib.parse_bill(self.CSV.encode("utf-8"), "微信.csv")
        pv = ib.build_previews(self.conn, self.lid, flows)
        return pv, ib.apply_import(self.conn, self.lid, pv, "微信.csv", "wechat", False)

    def _refund_row_id(self):
        return next(r["id"] for r in self.conn.execute(
            "SELECT id FROM transactions WHERE type='transfer'"))

    def test_batch_refund_backfill_with_skipped_rows(self):
        """批内退款回填错位（真机错链根因）：退款对之前存在被跳过的行（手动配对标 skip）
        时，旧版用预览序号直索引已插行列表 → 退款链到错误的行。修复后按预览序号映射。"""
        self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=3500,
                           date="2026-08-24 12:14:00", name="手记的35", source="app")
        csv = ("微信昵称：测试\n"
               "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号\n"
               "2026-08-24 12:14:30,转账,/,转账备注:垫付,支出,35.00,零钱,支付成功,MAN1,M3\n"
               "2026-08-25 12:15:03,微信红包-退款,/,/,收入,35.00,零钱,已退款,REF1,M1\n"
               "2026-08-24 12:15:00,微信红包（单发）,发给李星军,/,支出,35.00,零钱,支付成功,ORI1,M2\n"
               "2026-08-26 09:00:00,商户消费,肯德基,早餐,支出,12.00,零钱,支付成功,F1,M4\n")
        flows = ib.parse_bill(csv.encode("utf-8"), "微信.csv")
        pv = ib.build_previews(self.conn, self.lid, flows)
        self.assertTrue(pv[0].get("pay_dupe"))           # 首行被手动配对跳过（制造错位）
        ib.apply_import(self.conn, self.lid, pv, "微信.csv", "wechat", False)
        rid = conn_id = self.conn.execute(
            "SELECT id FROM transactions WHERE order_no='REF1'").fetchone()["id"]
        li_id = self.conn.execute(
            "SELECT id FROM transactions WHERE order_no='ORI1'").fetchone()["id"]
        row = self.conn.execute("SELECT refund_for FROM transactions WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["refund_for"], li_id)       # 链到李星军那笔，不是错位行


class TestJDNewFormat(EnvCase):
    """京东新导出：退款独立成行（退款-前缀+退款成功），括号仅标在原消费上——
    expand 括号行只取原消费不拆退款（防与独立行双计）。"""

    def _flows(self):
        import pathlib
        data = (pathlib.Path(__file__).parent / "fixtures" / "bills" / "jd_new.csv").read_bytes()
        return ib.parse_bill(data, "京东交易流水(新导出).csv")

    def test_origin_mode_no_duplicate_refund(self):
        flows = self._flows()
        self.assertEqual([f.get("skip") for f in flows if "skip" in f],
                         ["0 元行（先享后付等）"])                     # 0 元行跳过
        refunds = [f for f in flows if f.get("is_refund")]
        origins = [f for f in flows if f.get("type") == "expense" and "skip" not in f]
        self.assertEqual(len(refunds), 3)        # 仅三条独立退款行——括号不再拆出（防双计）
        self.assertEqual(len(origins), 4)        # 含两笔全额退的括号行（强制 expense 供退款关联）
        self.assertFalse(any(f.get("hash_fields", [""])[-1].startswith("jd_refund:")
                             for f in flows))

    def test_full_refund_pairs_high_partial_unmatched(self):
        pv = ib.build_previews(self.conn, self.lid, self._flows())
        by_name = lambda s: next(p for p in pv if (p.get("name") or "").startswith(s))
        ref_sn, org_sn = by_name("退款-松能"), by_name("松能")
        self.assertEqual((ref_sn["refund_for"], ref_sn["refund_confidence"]),
                         (-(pv.index(org_sn) + 1), "high"))           # 名称前缀+等额 → high
        ref_kq = by_name("退款-酷骑")
        self.assertEqual(ref_kq["refund_confidence"], "high")
        self.assertEqual(by_name("退款-京斯坦")["refund_for"],
                         -(pv.index(by_name("罗技")) + 1))
        self.assertEqual(by_name("退款-京斯坦")["refund_confidence"], "low")
        self.assertTrue(by_name("退款-京斯坦")["need_confirm"])

    def test_bracket_ambiguous_gives_up(self):
        """括号退款额同额歧义（两单都标了相同退款额）→ 放弃回待确认，宁低勿错。"""
        csv = ("导出信息：\n京东账号名：测试\n\n"
               "交易时间,商户名称,交易说明,金额,收/付款方式,交易状态,收/支,交易分类,交易订单号,商家订单号,备注\n"
               "2026-10-02 17:11:14,京东平台商户,退款-子件,26.72,储蓄卡(1234),退款成功,不计收支,网购,R1,B1,\n"
               "2026-10-02 17:10:40,京东平台商户,甲商品 等多件,100.00(已退款26.72),储蓄卡(1234),交易成功,支出,网购,O1,B2,\n"
               "2026-10-02 17:09:40,京东平台商户,乙商品 等多件,90.00(已退款26.72),储蓄卡(1234),交易成功,支出,网购,O2,B3,\n")
        flows = ib.parse_bill(csv.encode("utf-8"), "京东.csv")
        pv = ib.build_previews(self.conn, self.lid, flows)
        self.assertEqual(pv[0]["refund_confidence"], "unmatched")

    def test_bracket_cross_batch_from_db_tags(self):
        """跨批：先导含括号的原单（bracket_refund 随 tags 入库），退款行后到 →
        从库行 tags 解析括号额完成关联（low 待确认）。"""
        csv = ("导出信息：\n京东账号名：测试\n\n"
               "交易时间,商户名称,交易说明,金额,收/付款方式,交易状态,收/支,交易分类,交易订单号,商家订单号,备注\n"
               "2026-10-02 17:10:40,京东平台商户,罗技鼠标 等多件,152.88(已退款26.72),储蓄卡(1234),交易成功,支出,网购,O1,B2,\n")
        flows = ib.parse_bill(csv.encode("utf-8"), "京东.csv")
        pv = ib.build_previews(self.conn, self.lid, flows)
        ib.apply_import(self.conn, self.lid, pv, "京东.csv", "jd", False)
        eid = self.conn.execute("SELECT id FROM transactions WHERE type='expense'").fetchone()["id"]
        self.assertIsNotNone(self.conn.execute(
            "SELECT tags FROM transactions WHERE id=?", (eid,)).fetchone()[0])  # 括号额已随 tags 入库
        refund_csv = csv.replace(
            "2026-10-02 17:10:40,京东平台商户,罗技鼠标 等多件,152.88(已退款26.72),储蓄卡(1234),交易成功,支出,网购,O1,B2,",
            "2026-10-05 10:00:00,京东平台商户,退款-儿童牙刷,26.72,储蓄卡(1234),退款成功,不计收支,网购,R1,B9,")
        pv2 = ib.build_previews(self.conn, self.lid,
                                ib.parse_bill(refund_csv.encode("utf-8"), "京东.csv"))
        self.assertEqual((pv2[0]["refund_for"], pv2[0]["refund_confidence"]), (eid, "low"))


if __name__ == "__main__":
    unittest.main()
