"""数据层冒烟：映射语义、统计口径（退款扣减）、公共流水查询、行转换与增删改。"""
import sys
import unittest
from base import EnvCase, SRC

sys.path.insert(0, str(SRC))


class TestUpsertMapping(EnvCase):
    def test_batch_semantics(self):
        c, lid = self.conn, self.lid
        cid = self.topCat("餐饮美食")
        self.dbm.upsert_mapping(c, lid, "expense", "测试源", cid)          # INSERT 无批次
        row = c.execute("SELECT * FROM category_mappings WHERE source='测试源'").fetchone()
        self.assertIsNone(row["batch_id"])
        self.dbm.upsert_mapping(c, lid, "expense", "测试源", cid, batch_id=9)  # UPDATE 带批次 → 覆盖
        row = c.execute("SELECT * FROM category_mappings WHERE source='测试源'").fetchone()
        self.assertEqual(row["batch_id"], 9)
        self.dbm.upsert_mapping(c, lid, "expense", "测试源", cid)          # UPDATE 不带批次 → 保留
        row = c.execute("SELECT * FROM category_mappings WHERE source='测试源'").fetchone()
        self.assertEqual(row["batch_id"], 9)


class TestStatsRefund(EnvCase):
    def _seed(self):
        c, lid = self.conn, self.lid
        cid = self.topCat("餐饮美食")
        a = self.dbm.insert_tx(c, lid, type="expense", amount=10000, category_id=cid, date="2026-09-01")
        r = self.dbm.insert_tx(c, lid, type="transfer", amount=4000, date="2026-09-02")
        self.dbm.insert_tx(c, lid, type="income", amount=5000, date="2026-09-03")
        c.execute("UPDATE transactions SET refund_for=?, refund_status='partial' WHERE id=?", (a, r))
        c.commit()
        return cid

    def test_summary_range_refund_deduction(self):
        self._seed()
        s = self.dbm.stats_summary_range(self.conn, self.lid, "2026-09-01", "2026-10-01")
        self.assertEqual(s, {"gross_expense": 10000, "refund_deduction": 4000,
                             "net_expense": 6000, "income": 5000})

    def test_trend_monthly(self):
        self._seed()
        tr = self.dbm.stats_trend(self.conn, self.lid, "2026-09-01", "2026-10-01")
        self.assertEqual(tr[0], {"month": "2026-09", "gross": 10000, "income": 5000, "net": 6000})

    def test_yearly_net(self):
        self._seed()
        yr = self.dbm.stats_yearly(self.conn, self.lid)
        self.assertEqual(yr[0]["year"], "2026")
        self.assertEqual(yr[0]["net"], 6000)


class TestJloads(unittest.TestCase):
    def test_jloads(self):
        import db
        self.assertEqual(db.jloads(None, default={}), {})
        self.assertEqual(db.jloads("", 7), 7)
        self.assertEqual(db.jloads('{"a":1}'), {"a": 1})
        self.assertIsNone(db.jloads("{bad"))          # 坏 JSON → default(None)


class TestTxToDict(EnvCase):
    def test_tx_to_dict_keeps_raw_on_bad_json(self):
        import db
        c, lid = self.conn, self.lid
        tid = db.insert_tx(c, lid, type="expense", amount=100, date="2026-09-01",
                           tags={"k": "v"}, ai_meta={"m": 1})
        row = c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        d = db.tx_to_dict(row)
        self.assertEqual(d["tags"], {"k": "v"})
        c.execute("UPDATE transactions SET tags='{bad' WHERE id=?", (tid,))
        d2 = db.tx_to_dict(c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone())
        self.assertEqual(d2["tags"], "{bad")           # 坏 JSON 原样保留


class TestFetchTxRows(EnvCase):
    def test_inner_join_and_filters(self):
        c, lid = self.conn, self.lid
        cid = self.topCat("餐饮美食")
        dbm = self.dbm
        dbm.insert_tx(c, lid, type="expense", amount=100, category_id=cid, date="2026-09-01", name="有分类")
        dbm.insert_tx(c, lid, type="expense", amount=200, date="2026-09-02", name="无分类")
        c.commit()
        rows = dbm.fetch_tx_rows(c, lid, flow="expense", dfrom="2000-01-01", dto="2100-01-01")
        self.assertEqual(len(rows), 1)                          # INNER JOIN：无分类行不进列表（既有口径）
        self.assertEqual(rows[0]["name"], "有分类")
        self.assertEqual(dbm.count_tx_rows(c, lid, flow="expense"), 1)
        hit = dbm.fetch_tx_rows(c, lid, flow="expense", category="餐饮美食",
                                limit=5, over_count=True)
        self.assertEqual(hit[0]["cnt"], 1)                      # COUNT OVER 总数

    def test_insert_patch_delete_roundtrip(self):
        c, lid = self.conn, self.lid
        dbm = self.dbm
        a = dbm.insert_tx(c, lid, type="expense", amount=1000, name="原消费", date="2026-09-01")
        r = dbm.insert_tx(c, lid, type="transfer", amount=1000, name="退款", date="2026-09-02")
        c.execute("UPDATE transactions SET refund_for=? WHERE id=?", (a, r))
        self.assertTrue(dbm.patch_tx(c, r, {"amount": 800, "name": "部分退款"}))
        row = c.execute("SELECT amount,name FROM transactions WHERE id=?", (r,)).fetchone()
        self.assertEqual((row["amount"], row["name"]), (800, "部分退款"))
        path = dbm.delete_tx(c, a)                               # 删原消费 → 退款解链
        self.assertIsNone(path)
        left = c.execute("SELECT refund_for FROM transactions WHERE id=?", (r,)).fetchone()
        self.assertIsNone(left["refund_for"])


if __name__ == "__main__":
    unittest.main()
