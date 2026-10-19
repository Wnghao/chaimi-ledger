"""预览级黄金快照（IMPORT_REFACTOR_PLAN §四）：带状态账本上冻结 build_previews 输出。

与解析级快照（test_golden_snapshots）分工：那份护「文件→RawFlow」，本份护
「RawFlow→FlowPreview」——分类瀑布/去重/退款匹配/跨源配对/手动配对/疑似提示
全部落在这一层。此后的分类重构与模块拆分都以本快照零差异为验收线。

再生成（仅在有意变更行为时）：python tests/test_golden_previews.py --regen
"""
import json
import pathlib
import sys
import unittest

from base import EnvCase, SRC

sys.path.insert(0, str(SRC))
import import_bill as ib  # noqa: E402

BILLS = pathlib.Path(__file__).parent / "fixtures" / "bills"
SNAP_DIR = pathlib.Path(__file__).parent / "fixtures" / "preview_snapshots"

CASES = [
    ("meituan.csv", "美团账单(20260713-20261013).csv"),
    ("alipay_gbk.csv", "支付宝账单(20260701-20260930).csv"),
    ("alipay_noremark_gbk.csv", "支付宝账单(无备注列).csv"),
    ("wechat.xlsx", "微信支付账单(20260701-20260930).xlsx"),
    ("jd_new.csv", "京东交易流水(新导出).csv"),
    ("export_new.csv", "chaimi-ledger-导出(202609).csv"),
    ("export_old.csv", "chaimi-ledger-旧版导出(202609).csv"),
]


def seed_state(case: EnvCase):
    """带状态的账本（插入顺序固定 → id 确定性）：学习映射/手动记账/渠道行/
    同日同额组/已关联退款对，覆盖分类②级、手动配对、跨源正向、疑似重复。"""
    conn, dbm, lid = case.conn, case.dbm, case.lid
    with conn:
        cid = dbm.top_cat_id(conn, lid, "文化休闲", "expense")
        dbm.upsert_mapping(conn, lid, "expense", "文教", cid, learned=1)
        dbm.insert_tx(conn, lid, type="income", amount=260000,
                      date="2026-10-10 07:34:00", name="房租", source="app")
        dbm.insert_tx(conn, lid, type="expense", amount=2246,
                      date="2026-09-29 20:58:00", name="美团平台", merchant="美团平台",
                      source="wechat", order_no="WX_MT_OLD")
        for i, (m, t) in enumerate([("城南停车场", "09:01:00"), ("城北停车场", "12:30:00"),
                                    ("江边停车场", "18:05:00")]):
            dbm.insert_tx(conn, lid, type="expense", amount=500,
                          date=f"2026-10-18 {t}", name="停车费", merchant=m,
                          source="wechat", order_no=f"PKG{i}")
        eid = dbm.insert_tx(conn, lid, type="expense", amount=3000,
                            date="2026-09-05 10:00:00", name="纯牛奶一箱", source="app")
        dbm.insert_tx(conn, lid, type="transfer", amount=3000,
                      date="2026-09-10 10:00:00", name="退款-纯牛奶",
                      tags={"transfer_sub": "退款"}, refund_for=eid,
                      refund_status="full", refund_confidence="high", source="alipay")


def _build(case: EnvCase, fixture: str, filename: str):
    flows = ib.parse_bill((BILLS / fixture).read_bytes(), filename)
    return ib.build_previews(case.conn, case.lid, flows)


class TestGoldenPreviews(EnvCase):
    def test_snapshots_exist_and_match(self):
        missing = [f for f, _ in CASES if not (SNAP_DIR / f"{f}.json").is_file()]
        self.assertFalse(missing, f"缺快照（先 --regen）：{missing}")
        for fixture, filename in CASES:
            with self.subTest(fixture=fixture):
                expected = json.loads((SNAP_DIR / f"{fixture}.json").read_text("utf-8"))
                got = json.loads(json.dumps(_build(self, fixture, filename),
                                            ensure_ascii=False))
                self.assertEqual(got, expected)


def _regen() -> None:
    # 复跑两次比对，确认 seed 顺序带来的 id 全部确定（不确定的规范化过才能冻结）
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    import tempfile
    first = {}
    case = TestGoldenPreviews("test_snapshots_exist_and_match")
    case.setUp()
    try:
        for fixture, filename in CASES:
            # 每份账单独立账本状态：build_previews 会建户/写映射？只建户——独立跑互不污染 id 序
            first[fixture] = json.dumps(_build(case, fixture, filename), ensure_ascii=False, sort_keys=False)
        # 第二遍（重导同账单，另起环境）验证确定性
        case.tearDown()
        case.setUp()
        for fixture, filename in CASES:
            again = json.dumps(_build(case, fixture, filename), ensure_ascii=False, sort_keys=False)
            assert again == first[fixture], f"{fixture} 输出不确定，禁止冻结"
        for fixture, filename in CASES:
            (SNAP_DIR / f"{fixture}.json").write_text(
                json.dumps(json.loads(first[fixture]), ensure_ascii=False, indent=1), "utf-8")
            print(f"preview snapshot {fixture}: {len(json.loads(first[fixture]))} rows")
    finally:
        case.tearDown()


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regen()
    else:
        unittest.main()
