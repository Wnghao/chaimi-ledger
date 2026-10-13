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

    def test_expense_and_refund(self):
        flows = ib.parse_alipay(self.ALI)
        f0, f1 = flows[0], flows[1]
        self.assertEqual(f0["type"], "expense")
        self.assertEqual(f0["amount"], 2550)
        self.assertEqual(f1["type"], "transfer")          # 退款强制 transfer
        self.assertTrue(f1["is_refund"])
        self.assertEqual(f1["amount"], 9900)
        self.assertEqual(f1["note"], "某商户 退款备注")     # 备注列回退最后取到

    def test_zero_amount_skipped(self):
        flows = ib.parse_alipay(self.ALI)
        self.assertEqual(len(flows), 3)                   # 0 元行产出 skip 标记行
        self.assertEqual(flows[2]["skip"], "0 元退款行")
        self.assertTrue(all("hash_fields" in f for f in flows[:2]))

    def test_hash_amount_normalized_to_cents(self):
        f0 = ib.parse_alipay(self.ALI)[0]
        keys = ["交易时间", "交易分类", "交易对方", "商品说明", "金额", "商家订单号", "备注"]
        self.assertEqual(f0["hash_fields"][keys.index("金额")], "2550")


class TestJD(EnvCase):
    JD = ("交易时间,商户名称,交易说明,金额,收/支,交易状态,交易分类,交易订单号,收/付款方式,备注\n"
          "2026-09-03 12:00:00,京东自营,商品A,100(已退款30),支出,已完成,数码电器,JD1,京东支付,\n"
          "2026-09-04 12:00:00,京东自营,商品B,50(已退积分),支出,已完成,日用百货,JD2,京东支付,\n")

    def test_bracket_refund_split(self):
        fj = ib.parse_jd(self.JD)
        self.assertEqual(len(fj), 3)                      # 拆行2 + 括号待确认1
        origin, refund = fj[0], fj[1]
        self.assertEqual((origin["type"], origin["amount"]), ("expense", 10000))
        self.assertEqual((refund["type"], refund["amount"]), ("transfer", 3000))
        self.assertTrue(refund["is_refund"])
        self.assertEqual(refund["hash_fields"][-1], "jd_refund:3000")   # 拆分行靠尾标区分
        self.assertEqual(origin["hash_fields"][4], "10000")             # 拆分后金额规范化

    def test_non_refund_paren_marks_confirm(self):
        fj = ib.parse_jd(self.JD)
        paren = fj[2]                                     # 第三条是括号待确认行
        self.assertTrue(paren.get("need_confirm"))
        self.assertFalse(paren.get("is_refund"))


class TestAppExport(EnvCase):
    EX = ("交易时间,交易名称,交易金额,流水类型,支付账户,收款账户,支出类型/收入类型,备注,订单号,来源,流水ID,退款关联,流水归属\n"
          "2026-09-05 09:00:00,午餐,30.00,支出,现金,,餐饮美食,,E1,app,T1,,小明\n"
          "2026-09-05 12:00:00,退货,30.00,不计收支,现金,,退款,,E2,app,T2,T1,小明\n")

    def test_refund_roundtrip_by_id(self):
        fe = ib.parse_app_export(self.EX)
        self.assertEqual(fe[0]["export_id"], "T1")
        self.assertEqual(fe[1]["export_refund_idx"], -1)   # 批内直连负序号协议
        self.assertEqual(fe[0]["hash_fields"][2], "3000")  # 金额列规范化


class TestWechatHelpers(unittest.TestCase):
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
            ib.parse_alipay(head + body * (ib.MAX_PARSE_ROWS + 500))


if __name__ == "__main__":
    unittest.main()
