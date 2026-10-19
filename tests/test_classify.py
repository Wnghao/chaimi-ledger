"""分类瀑布各阶段单测（IMPORT_REFACTOR_PLAN §三：每级独立可测，行为由预览快照整体锁定）。"""
import sys
import unittest

from base import EnvCase, SRC

sys.path.insert(0, str(SRC))
import import_bill as ib  # noqa: E402
from import_classify import ClassifyCtx, classify_transfer, classify_flow  # noqa: E402


def _flow(**kw):
    base = {"type": "expense", "name": "", "merchant": "", "note": "", "amount": 1000,
            "source_category": "", "platform": "alipay"}
    base.update(kw)
    return base


def _preview(f):
    return {"type": f["type"], "need_confirm": False, "tags": {},
            "category_id": None, "category_name": "", "parent_category": "",
            "classified_via": "raw"}


class TestClassifyTransfer(EnvCase):
    def test_known_subclass_and_refund(self):
        ctx = ClassifyCtx(self.conn, self.lid)
        p = _preview(_flow(type="transfer", source_category="信用卡还款"))
        classify_transfer(_flow(type="transfer", source_category="信用卡还款"), ctx, p)
        self.assertEqual((p["tags"]["transfer_sub"], p["classified_via"]), ("信用卡还款", "transfer"))
        self.assertFalse(p["need_confirm"])
        p2 = _preview(_flow(type="transfer", is_refund=True))
        classify_transfer(_flow(type="transfer", is_refund=True), ctx, p2)
        self.assertEqual(p2["tags"]["transfer_sub"], "退款")

    def test_unknown_subclass_needs_confirm(self):
        ctx = ClassifyCtx(self.conn, self.lid)
        f = _flow(type="transfer", source_category="全新理财类型")
        p = _preview(f)
        classify_transfer(f, ctx, p)
        self.assertEqual(p["tags"]["transfer_sub"], "账户转账")
        self.assertTrue(p["need_confirm"])

    def test_export_refund_link(self):
        ctx = ClassifyCtx(self.conn, self.lid)
        f = _flow(type="transfer", platform="export", export_refund_idx=-1, amount=3000)
        p = _preview(f)
        classify_transfer(f, ctx, p, export_tgt_amount=3000)
        self.assertEqual((p["refund_for"], p["refund_confidence"], p["refund_status"]),
                         (-1, "high", "full"))
        self.assertFalse(p["need_confirm"])


class TestClassifyFlow(EnvCase):
    def test_keyword_plate(self):
        ctx = ClassifyCtx(self.conn, self.lid)
        f = _flow(name="停车费 京A12345")
        p = _preview(f)
        classify_flow(f, ctx, p)
        self.assertEqual((p["classified_via"], p["parent_category"]), ("keyword", "爱车养车"))

    def test_mapping_hit(self):
        cid = self.dbm.top_cat_id(self.conn, self.lid, "文化休闲", "expense")
        self.dbm.upsert_mapping(self.conn, self.lid, "expense", "文教", cid, learned=1)
        ctx = ClassifyCtx(self.conn, self.lid)
        f = _flow(source_category="文教", name="某书")
        p = _preview(f)
        classify_flow(f, ctx, p)
        self.assertEqual((p["classified_via"], p["category_id"]), ("mapping", cid))
        self.assertEqual(p["map_source"], "文教")            # 纠正回写的学习键

    def test_mapping_to_transfer_group(self):
        cid = self.dbm.top_cat_id(self.conn, self.lid, "退款", "transfer")
        self.dbm.upsert_mapping(self.conn, self.lid, "expense", "某渠道退款", cid, learned=1)
        ctx = ClassifyCtx(self.conn, self.lid)
        f = _flow(source_category="某渠道退款")
        p = _preview(f)
        classify_flow(f, ctx, p)
        self.assertEqual(p["type"], "transfer")              # 整行转不计收支
        self.assertEqual(p["tags"]["transfer_sub"], "退款")
        self.assertFalse(p["need_confirm"])

    def test_direct_name(self):
        ctx = ClassifyCtx(self.conn, self.lid)
        f = _flow(platform="export", source_category="餐饮美食", name="午餐")
        p = _preview(f)
        classify_flow(f, ctx, p)
        self.assertEqual((p["classified_via"], p["parent_category"]), ("direct-name", "餐饮美食"))

    def test_fallback_raw_needs_confirm(self):
        ctx = ClassifyCtx(self.conn, self.lid)
        f = _flow(name="完全无法识别的东西")
        p = _preview(f)
        classify_flow(f, ctx, p)
        self.assertEqual((p["classified_via"], p["parent_category"]), ("raw", "其他"))
        self.assertTrue(p["need_confirm"])

    def test_renqing_review_on_mapping_result(self):
        """映射结果落到人情类 → 复核按小额红包规则二切（红包礼金小额）。"""
        cid = self.dbm.top_cat_id(self.conn, self.lid, "人情往来", "expense")
        self.dbm.upsert_mapping(self.conn, self.lid, "expense", "亲戚往来", cid, learned=1)
        ctx = ClassifyCtx(self.conn, self.lid)
        f = _flow(source_category="亲戚往来", name="红包", amount=2000)   # 20 元小额红包
        p = _preview(f)
        classify_flow(f, ctx, p)
        self.assertEqual(p["parent_category"], "红包礼金")


if __name__ == "__main__":
    unittest.main()
