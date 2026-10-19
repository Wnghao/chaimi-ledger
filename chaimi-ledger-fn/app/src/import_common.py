"""导入公共原语（各子模块共享的叶子工具，无内部依赖）。"""
from __future__ import annotations

import platform_templates as pt

def normalize(field) -> str:
    """trim（含京东/美团单元格尾部 \\t）。"""
    return str(field or "").strip().replace("\t", "")

_IN_CHUNK = 500

def _chunks(seq, size: int = _IN_CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

def _is_placeholder_name(name: str) -> bool:
    """占位名判定：微信账单「商品」列无内容时常写作 "/"（红包/转账行普遍如此），这类占位名
    不参与名称匹配：否则两条同样写作 "/" 的行会因「金额相等 + 前缀相等」被判为高置信，
    把退款关联到无关的红包/转账行。"""
    return not name.strip("/-—\\ \u3000")

def is_meaningless_name(name: str) -> bool:
    """名称是否无意义：空 / 占位符（"/"、"-"）/ 微信红包·转账这类泛化值。
    用于「对方优先 + AI 兜底」的名称回填判定。"""
    n = normalize(name)
    return _is_placeholder_name(n) or n in pt.WX_GENERIC_NAMES


def _is_new_row(p: dict) -> bool:
    """预览行是否入库（server 计数 / 退款候选池 / apply 两处共用）：非精确重复且未被跳过
    （skip 含跨源/手动配对/无效行）。"""
    return not p.get("duplicate") and not p.get("skip")
