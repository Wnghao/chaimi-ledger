"""server 单元：日期区间助手、路径守卫、退款关联统一入口（_link_refund 校验矩阵）。"""
import sys
import unittest
from base import EnvCase, SRC

sys.path.insert(0, str(SRC))
import server
import bot_core
from fastapi import HTTPException


class TestDateWhere(unittest.TestCase):
    def test_raw_exclusive(self):
        frag, params = server._date_where("2026-09-01", "2026-10-01")
        self.assertEqual(frag, " AND t.date>=? AND t.date<?")
        self.assertEqual(params, ["2026-09-01", "2026-10-01"])

    def test_inflate_exclusive(self):
        frag, params = server._date_where("", "2026-09-30", end="<")
        self.assertEqual(frag, " AND t.date<?")
        self.assertEqual(params, ["2026-09-30 23:59:59"])       # 10 位膨胀含当天

    def test_inflate_inclusive(self):
        frag, params = server._date_where("f", "2026-09-30", end="<=")
        self.assertEqual(frag, " AND t.date>=? AND t.date<=?")
        self.assertEqual(params, ["f", "2026-09-30 23:59:59"])

    def test_full_timestamp_passthrough(self):
        frag, params = server._date_where("", "2026-09-30 12:00:00", end="<")
        self.assertEqual(params, ["2026-09-30 12:00:00"])       # 非 10 位原样


class TestSafeName(unittest.TestCase):
    def test_rejects(self):
        for bad in ("", "..", "a/b", "a\\b", "../x"):
            with self.assertRaises(HTTPException):
                server._safe_name(bad)

    def test_passes(self):
        self.assertEqual(server._safe_name("icon-64.png"), "icon-64.png")


class TestReceiptMagic(unittest.TestCase):
    """小票上传魔数校验（bot_core.is_image，App/网关/微信直连三路共用）：
    JPG/PNG/WEBP 放行，任意字节（脚本/HTML/空）拒绝落盘。"""

    def test_image_formats_accepted(self):
        self.assertTrue(bot_core.is_image(b"\xff\xd8\xff\xe0" + b"\x00" * 32))          # JPEG
        self.assertTrue(bot_core.is_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32))         # PNG
        self.assertTrue(bot_core.is_image(b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16))  # WEBP

    def test_non_image_rejected(self):
        self.assertFalse(bot_core.is_image(b"#!/bin/sh\nrm -rf /"))
        self.assertFalse(bot_core.is_image(b"<html><script>alert(1)</script>"))
        self.assertFalse(bot_core.is_image(b""))


class TestTokenOk(unittest.TestCase):
    def test_constant_time_bearer_compare(self):
        self.assertTrue(server._token_ok("Bearer abc123", "abc123"))
        self.assertFalse(server._token_ok("Bearer abc124", "abc123"))   # 错 Token
        self.assertFalse(server._token_ok("abc123", "abc123"))          # 缺 Bearer 前缀
        self.assertFalse(server._token_ok("", "abc123"))
        self.assertFalse(server._token_ok("Token abc123", "abc123"))    # 非 Bearer 方案

    def test_non_ascii_header_fails_closed(self):
        # compare_digest 对非 ASCII str 会抛 TypeError：必须判失败而非 500
        self.assertFalse(server._token_ok("Bearer 中文头", "abc123"))


class TestBasicAuth(unittest.TestCase):
    CFG = {"username": "chaimi", "password": "s3cret"}

    @staticmethod
    def _hdr(user, pwd):
        import base64
        return "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()

    def test_ok_and_rejects(self):
        self.assertTrue(server._basic_auth_ok(self._hdr("chaimi", "s3cret"), self.CFG))
        self.assertFalse(server._basic_auth_ok(self._hdr("chaimi", "wrong"), self.CFG))    # 错密码
        self.assertFalse(server._basic_auth_ok(self._hdr("admin", "s3cret"), self.CFG))  # 错用户
        self.assertFalse(server._basic_auth_ok("Bearer abc", self.CFG))                  # 非 Basic
        self.assertFalse(server._basic_auth_ok("Basic !!!not-base64!!!", self.CFG))      # 坏 base64
        self.assertFalse(server._basic_auth_ok("Basic " + "6isOo2s6", self.CFG))         # 无冒号分隔

    def test_empty_creds_fail_closed(self):
        # enabled 但未配置凭证：空用户/密码恒拒绝（fail-closed，强制管理员补齐）
        self.assertFalse(server._basic_auth_ok(self._hdr("", ""), {"username": "", "password": ""}))


class TestExportNeutralize(EnvCase):
    """公式注入中和（复查项①）：账单/用户可控文本以 = + - @ 开头时导出单元格前缀单引号。"""

    def _row(self, **kw):
        base = {"date": "2026-09-01", "type": "expense", "amount": 100, "name": "早餐",
                "category": "餐饮美食", "account": "支付宝", "to_account": "", "note": "",
                "order_no": "", "source": "alipay", "id": 1, "refund_for": "", "attr_display": ""}
        base.update(kw)
        return base

    def test_formula_cells_prefixed(self):
        table = server._export_table([self._row(
            name='=HYPERLINK("http://evil","点我")', note="+1123", attr_display="-1+1",
            order_no="@cmd")], self.lid)
        row = table[1]
        self.assertEqual(row[1], '\'=HYPERLINK("http://evil","点我")')   # 交易名称
        self.assertEqual(row[9], "'-1+1")                                # 流水归属
        self.assertEqual(row[10], "'+1123")                              # 备注
        self.assertEqual(row[11], "'@cmd")                               # 订单号
        self.assertEqual(row[6], "支付宝")                               # 非公式开头不动

    def test_normal_cells_untouched(self):
        table = server._export_table([self._row(name="早餐", note="肯德基")], self.lid)
        row = table[1]
        self.assertEqual(row[1], "早餐")
        self.assertEqual(row[10], "肯德基")
        self.assertEqual(row[13], 1)                                     # 流水ID 原样


class TestLinkRefund(EnvCase):
    def _mk(self, ttype="expense", amount=5000, date="2026-09-05", ledger=None):
        lid = ledger if ledger is not None else self.lid
        return self.dbm.insert_tx(self.conn, lid, type=ttype, amount=amount,
                                  date=date, name="x")

    def test_cancel_clears(self):
        r = self._mk(ttype="transfer")
        o = self._mk()
        self.conn.execute("UPDATE transactions SET refund_for=? WHERE id=?", (o, r))
        server._link_refund(self.conn, r, 0, 0, confidence=None)
        row = self.conn.execute("SELECT refund_for, refund_status, refund_confidence FROM transactions WHERE id=?", (r,)).fetchone()
        self.assertIsNone(row["refund_for"])
        self.assertEqual(row["refund_status"], "none")

    def test_full_partial_and_type(self):
        o = self._mk(amount=5000)
        r = self._mk(ttype="income", amount=3000, date="2026-09-06")
        server._link_refund(self.conn, r, o, 3000, confidence="high")
        row = self.conn.execute("SELECT refund_for, refund_status, refund_confidence, type FROM transactions WHERE id=?", (r,)).fetchone()
        self.assertEqual(row["refund_for"], o)
        self.assertEqual(row["refund_status"], "partial")       # 3000 < 5000
        self.assertEqual(row["refund_confidence"], "high")
        self.assertEqual(row["type"], "transfer")               # 关联后强制不计收支

    def test_rejects_cross_ledger_and_non_expense(self):
        cur = self.conn.execute("INSERT INTO ledgers(name) VALUES('另一本')")
        other = cur.lastrowid
        o_foreign = self._mk(ledger=other)
        r = self._mk(ttype="transfer", date="2026-09-06")
        with self.assertRaises(HTTPException):                   # 跨账本
            server._link_refund(self.conn, r, o_foreign, 100, confidence=None)
        income = self._mk(ttype="income", date="2026-09-06")
        with self.assertRaises(HTTPException):                   # 目标非支出
            server._link_refund(self.conn, r, income, 100, confidence=None)

    def test_rejects_late_origin(self):
        o = self._mk(date="2026-09-10")
        r = self._mk(ttype="transfer", date="2026-09-05")
        with self.assertRaises(HTTPException):                   # 原消费晚于退款
            server._link_refund(self.conn, r, o, 100, confidence=None)


if __name__ == "__main__":
    unittest.main()
