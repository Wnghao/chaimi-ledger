"""黄金快照（计划书 P0/§八）：冻结各平台账单解析输出（RawFlow 逐字段，含 skip 行与 hash_fields），
作为解析层改造的长期回归基线。改造前由旧解析器生成；此后任何解析路径变更都须与此快照逐字段等价。

再生成（仅在有意变更解析行为时）：python tests/test_golden_snapshots.py --regen
"""
import json
import pathlib
import sys
import unittest

from base import SRC

sys.path.insert(0, str(SRC))
import import_bill as ib  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
BILLS = FIXTURES / "bills"
SNAPSHOTS = FIXTURES / "snapshots"

# (fixture 文件, 传入 parse_bill 的文件名——文件名不参与识别，仅日志/批次记录)
CASES = [
    ("meituan.csv", "美团账单(20260713-20261013).csv"),
    ("alipay_gbk.csv", "支付宝账单(20260701-20260930).csv"),
    ("alipay_noremark_gbk.csv", "支付宝账单(无备注列).csv"),
    ("wechat.xlsx", "微信支付账单(20260701-20260930).xlsx"),
    ("jd_new.csv", "京东交易流水(新导出).csv"),
    ("export_new.csv", "chaimi-ledger-导出(202609).csv"),
    ("export_old.csv", "chaimi-ledger-旧版导出(202609).csv"),
]


def _canon(obj):
    """规范化为可比较/可序列化结构：dict 键排序（RawFlow 键序无关），列表保序。"""
    if isinstance(obj, dict):
        return {k: _canon(obj[k]) for k in sorted(obj)}
    if isinstance(obj, (list, tuple)):
        return [_canon(x) for x in obj]
    return obj


def _parse(fixture: str, filename: str):
    data = (BILLS / fixture).read_bytes()
    return _canon(ib.parse_bill(data, filename))


class TestGoldenSnapshots(unittest.TestCase):
    def test_fixtures_exist(self):
        for fixture, _ in CASES:
            self.assertTrue((BILLS / fixture).is_file(), f"缺少 fixture：{fixture}")
            self.assertTrue((SNAPSHOTS / f"{fixture}.json").is_file(),
                            f"缺少快照（先运行 --regen 生成）：{fixture}.json")

    def test_parse_matches_snapshot(self):
        for fixture, filename in CASES:
            with self.subTest(fixture=fixture):
                expected = json.loads((SNAPSHOTS / f"{fixture}.json").read_text("utf-8"))
                self.assertEqual(_parse(fixture, filename), expected)


def _regen() -> None:
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    for fixture, filename in CASES:
        out = _parse(fixture, filename)
        (SNAPSHOTS / f"{fixture}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
        n = len(out)
        print(f"snapshot {fixture}: {n} flows")


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regen()
    else:
        unittest.main()
