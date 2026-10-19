"""备份保留策略 + AI 规则层 + bot 全链路（无 LLM Key 时走 fix_local 兜底的完整闭环）。"""
import datetime
import pathlib
import sys
import unittest
from base import EnvCase, SRC

sys.path.insert(0, str(SRC))


class TestBackupRetention(unittest.TestCase):
    def setUp(self):
        import backup
        self.backup = backup
        self._tmp = pathlib.Path(__import__("tempfile").mkdtemp())
        self._orig = backup.config.backup_dir
        backup.config.backup_dir = lambda: self._tmp
        self.now = datetime.datetime(2026, 10, 12)

    def tearDown(self):
        self.backup.config.backup_dir = self._orig

    def _mk(self, name):
        (self.tmpfile(name)).write_bytes(b"x")

    def tmpfile(self, name):
        return self._tmp / name

    def test_monthly_retention_removes_all_sides(self):
        old = "chaimi-ledger-202601.db"                      # 距 2026-10 超过 3 个月
        keep = "chaimi-ledger-202609.db"
        for n in (old, keep):
            self._mk(n)
            self.tmpfile(n.replace(".db", ".receipts.zip")).write_bytes(b"x")
            self.tmpfile(n.replace(".db", ".conf.json")).write_bytes(b"x")
        self.backup._cleanup_old(3, self.now, yearly=False)
        self.assertFalse(self.tmpfile(old).exists())           # 主文件
        self.assertFalse(self.tmpfile("chaimi-ledger-202601.receipts.zip").exists())  # 副文件同口径
        self.assertFalse(self.tmpfile("chaimi-ledger-202601.conf.json").exists())
        self.assertTrue(self.tmpfile(keep).exists())           # 保留期内不动

    def test_yearly_retention(self):
        old, keep = "chaimi-ledger-2022.db", "chaimi-ledger-2025.db"
        for n in (old, keep):
            self._mk(n)
            self.tmpfile(n.replace(".db", ".conf.json")).write_bytes(b"x")
        self.backup._cleanup_old(3, self.now, yearly=True)
        self.assertFalse(self.tmpfile(old).exists())
        self.assertTrue(self.tmpfile(keep).exists())

    def test_name_validation(self):
        self.assertTrue(self.backup._valid_name("chaimi-ledger-202601.db"))
        for bad in ("../x", "a/b", "other.db", "",
                    "chaimi-ledger-202601.conf.json",    # 副文件（含明文 Key）不可经 API 触达
                    "chaimi-ledger-202601.receipts.zip"):
            self.assertFalse(self.backup._valid_name(bad))

    def test_restore_rejects_non_sqlite_file(self):
        """复查项④：非 SQLite 文件恢复 → ValueError（server 映射 400），不再裸 500。"""
        (self._tmp / "chaimi-ledger-bad.db").write_bytes(b"definitely not a sqlite file")
        with self.assertRaises(ValueError):
            self.backup.restore_backup("chaimi-ledger-bad.db")


class TestAIRules(unittest.TestCase):
    def test_fix_local_multi_item(self):
        import ai
        items = ai.fix_local("午饭25 打车13")
        self.assertEqual([(i["name"], i["amount"]) for i in items], [("午饭", 2500), ("打车", 1300)])
        self.assertTrue(all(i["date"] for i in items))

    def test_fix_local_keyword_category(self):
        """离线兜底也自动分类（无 Key 场景）：餐时词/打车走关键词词典，不再全靠手动选。"""
        import ai
        items = ai.fix_local("早餐5元 打车15")
        by_name = {i["name"]: i for i in items}
        self.assertEqual(by_name["早餐"]["category_name"], "餐饮美食")
        self.assertEqual(by_name["打车"]["category_name"], "交通出行")

    def test_booking_items_kw_fallback(self):
        """LLM/兜底条目缺分类名时：关键词兜底补齐（词典同源，含红包两级归类）。"""
        import ai
        items = ai._booking_items([
            {"type": "expense", "amount": 500, "name": "午饭"},
            {"type": "expense", "amount": 20000, "name": "同事结婚", "note": "份子钱"},
            {"type": "expense", "amount": 3000, "name": "滴滴出行"},
        ], None, "")
        self.assertEqual(items[0]["category_name"], "餐饮美食")
        self.assertEqual(items[1]["category_name"], "人情往来")   # 200 元档以上份子
        self.assertEqual(items[2]["category_name"], "交通出行")

    def test_fix_local_income_and_decimal(self):
        import ai
        items = ai.fix_local("收到转账200 发了5000工资 早饭4块5")
        names = {i["name"]: i for i in items}
        self.assertEqual(names["转账"]["type"], "income")
        self.assertEqual(names["工资"]["type"], "income")
        self.assertEqual(names["早饭"]["amount"], 450)      # 「4块5」=4.5 元（块后孤数字按角补）

    def test_hour_minute(self):
        import ai
        self.assertEqual(ai._hour_minute("8点开会"), "08:00")
        self.assertEqual(ai._hour_minute("下午3点半"), "15:30")
        self.assertEqual(ai._hour_minute("中午吃饭"), "12:00")
        self.assertIsNone(ai._hour_minute("没时间词"))

    def test_gift_fix(self):
        import ai
        it = {"name": "份子钱", "note": ""}
        ai._gift_fix("10月3号小明儿子结婚去了5000", it)
        self.assertEqual(it["name"], "小明")
        self.assertEqual(it["note"], "儿子结婚")

    def test_booking_items_normalization(self):
        import ai
        items = ai._booking_items([{"type": "expense", "amount": "25元", "name": "早饭"},
                                   {"type": "expense", "amount": None}], None, "早饭25")
        self.assertEqual(len(items), 1)                     # 无金额的丢弃
        self.assertEqual(items[0]["amount"], 2500)          # 带单位字符串清洗转分

    def test_sse_stream_format(self):
        import ai
        out = list(ai.sse_stream(iter([{"text": "你好"}, {"error": "x"}])))
        self.assertEqual(out[0], 'data: {"text": "你好"}\n\n')
        self.assertEqual(out[-1], "data: [DONE]\n\n")


class TestProcessReceipt(unittest.TestCase):
    """小票裁剪+压缩：留白裁剪 / 缩放 / 重编码 / 安全兜底。"""

    def _mk_receipt(self, w=3000, h=4000, content_box=(800, 1200, 2200, 2800)):
        """构造模拟小票照片：大图中间有小块文字区域，四周是均匀背景。"""
        from PIL import Image, ImageDraw
        import tempfile, pathlib
        img = Image.new("RGB", (w, h), (200, 200, 200))   # 桌面灰色背景
        draw = ImageDraw.Draw(img)
        l, t, r, b = content_box
        draw.rectangle([l, t, r, b], fill=(255, 255, 255))  # 小票白色区域
        for y in range(t + 50, b - 50, 40):                  # 模拟文字行
            draw.line([(l + 30, y), (r - 30, y)], fill=(30, 30, 30), width=3)
        p = pathlib.Path(tempfile.mkdtemp()) / "test_receipt.jpg"
        img.save(p, "JPEG", quality=95)
        return p, w * h

    def test_crop_and_compress(self):
        import bot_core
        p, orig_area = self._mk_receipt()
        orig_size = p.stat().st_size
        self.assertTrue(bot_core.process_receipt(p))
        from PIL import Image
        with Image.open(p) as img:
            new_w, fmt = img.size[0], img.format
        # 缩放生效：原图 3000px 宽 → ≤1080px
        self.assertLessEqual(new_w, 1080)
        # 压缩生效：体积应显著小于原大图（4000×3000 高质量 JPEG）
        self.assertLess(p.stat().st_size, orig_size)
        self.assertEqual(fmt, "JPEG")

    def test_uniform_image_kept(self):
        """全均匀图像（无内容）→ 保留原图（安全兜底）。"""
        import bot_core
        from PIL import Image
        import tempfile, pathlib
        p = pathlib.Path(tempfile.mkdtemp()) / "blank.jpg"
        Image.new("RGB", (1000, 1000), (128, 128, 128)).save(p, "JPEG")
        before = p.stat().st_size
        # 不崩溃，返回 True（重编码了，但没裁剪——全图都是均匀的）
        result = bot_core.process_receipt(p)
        self.assertTrue(result)

    def test_nonexistent_file_no_crash(self):
        import bot_core
        self.assertFalse(bot_core.process_receipt("不存在的文件.jpg"))

    def test_small_content_not_cropped(self):
        """边界框面积 < 原图 10% → 保留原图（防误裁）。"""
        import bot_core
        p, _ = self._mk_receipt(content_box=(1450, 1950, 1550, 2050))  # 100×100 = 0.08%
        before_size = p.stat().st_size
        bot_core.process_receipt(p)
        from PIL import Image
        with Image.open(p) as img:
            width = img.size[0]
        # 没被裁到只剩一小块（宽高应接近原图或已缩放但比例不变）
        self.assertGreater(width, 500)


class TestBotChain(EnvCase):
    def test_draft_confirm_undo_without_llm(self):
        import bot_core
        bot_core._sessions.clear()
        r1 = bot_core.handle("午饭25 打车13", "utest", bypass_auth=True)
        self.assertIn("识别到 2 笔", r1)
        r2 = bot_core.handle("确认", "utest", bypass_auth=True)
        self.assertIn("已记入 2 笔", r2)
        n = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE ledger_id=? AND source='bot'",
                              (self.lid,)).fetchone()[0]
        self.assertEqual(n, 2)
        r3 = bot_core.handle("撤销", "utest", bypass_auth=True)
        self.assertIn("已撤销", r3)
        bot_core._sessions.clear()

    def test_list_view_pagination_states(self):
        import bot_core
        s = bot_core._session("utest2")
        s["query"] = {"dfrom": "2000-01-01", "dto": "2100-01-01", "flow": "expense"}
        cid = self.topCat("餐饮美食")
        self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=100,
                           category_id=cid, date="2026-09-01", name="咖啡")
        self.conn.commit()
        view = bot_core._list_view(s, self.lid, bot_core.LIST_MAX)
        self.assertIn("咖啡", view)
        s["query"]["more"] = True
        view2 = bot_core._list_view(s, self.lid, bot_core.ALL_MAX)
        self.assertIn("共1笔", view2)
        bot_core._sessions.clear()

    def test_collect_events(self):
        import bot_core
        out = bot_core._collect(iter([{"text": "a"}, {"text": "b"}, {"stats": {"k": 1}}, {"error": "e"}]))
        self.assertEqual(out["text"], "ab")
        self.assertEqual(out["stats"], {"k": 1})
        self.assertEqual(out["error"], "e")
        self.assertIsNone(out["booking"])


class TestRunToolCategoryMatch(EnvCase):
    def test_short_name_normalized_to_canonical(self):
        import ai
        cid = self.topCat("餐饮美食")
        self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=3000,
                           category_id=cid, date="2026-09-05", name="午饭")
        self.conn.commit()
        _, attach = ai._run_tool(self.lid, {"kind": "category", "category": "餐饮",
                                            "range": "month", "month": "2026-09"})
        self.assertTrue(attach["matched"])
        self.assertEqual(attach["category"], "餐饮美食")          # 简称归一化到规范名
        self.assertEqual(attach["total"], 1)
        self.assertEqual(attach["items"][0]["name"], "午饭")      # 旧版此处恒空（R-06 口径分叉）

    def test_unmatched_no_items(self):
        import ai
        _, attach = ai._run_tool(self.lid, {"kind": "category", "category": "不存在的分类",
                                            "range": "month", "month": "2026-09"})
        self.assertFalse(attach["matched"])
        self.assertNotIn("items", attach)


if __name__ == "__main__":
    unittest.main()
