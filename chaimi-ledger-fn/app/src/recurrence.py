"""周期自动记账：到期规则生成流水（DESIGN 外新功能，AutoBackup 同款线程模型）。

线程：daemon 线程 60s 轮询，每 tick 现查库（规则是业务数据，改完免重启）；
幂等：流水 row_hash = md5("recur|{rule_id}|{next_date}")，确定性 hash——重复
触发/并发/重启都不会双记同一期（insert_tx 的时间戳 hash 只适用于人工路径）；
补跑刻意不做（浩拍板）：每次到期只生成 next_date 当期一笔再推进，停机漏掉
的周期不回补——漏了就漏了，避免长期停机后爆量重复。

纯函数（next_period / fire_rule）与线程解耦，单测直接驱动。
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta

import applog

_log = applog.get("recurrence")

FREQUENCIES = ("daily", "weekly", "monthly")
_MAX_INTERVAL = 365   # 间隔上限（防手滑填 99999 月把 next_date 推到千年后）


def next_period(d: str, frequency: str, interval: int = 1,
                weekday: int | None = None, day_of_month: int | None = None) -> str:
    """从周期日 d 推进到下一个周期日（纯函数，返回 YYYY-MM-DD）。
    weekly 保持 weekday（推进时按 N 周对齐）；monthly 保持 day_of_month，
    超当月天数钳到月末（31 日规则在 2 月生成 28/29 日，3 月回到 31 日）。"""
    n = max(1, int(interval or 1))
    cur = date.fromisoformat(d)
    if frequency == "daily":
        return (cur + timedelta(days=n)).isoformat()
    if frequency == "weekly":
        # 以 d 为锚点按 N 周推进（weekday 字段用于创建时对齐首个到期日，推进保持锚点节奏）
        return (cur + timedelta(weeks=n)).isoformat()
    if frequency == "monthly":
        m = cur.month - 1 + n
        year = cur.year + m // 12
        month = m % 12 + 1
        dom = day_of_month or cur.day
        # 钳到目标月最后一天
        if month == 12:
            last = (date(year + 1, 1, 1) - timedelta(days=1)).day
        else:
            last = (date(year, month + 1, 1) - timedelta(days=1)).day
        return date(year, month, min(dom, last)).isoformat()
    raise ValueError(f"未知周期类型：{frequency}")


def row_hash(rule_id: int, period_date: str) -> str:
    """确定性流水 hash：同规则同周期日恒同 hash，判重天然幂等。"""
    return hashlib.md5(f"recur|{rule_id}|{period_date}".encode()).hexdigest()


def _fire(c: sqlite3.Connection, rule: sqlite3.Row, period: str, today: str) -> int | None:
    """按指定周期日生成当期流水并推进 next_date（fire_rule/fire_now 共用实现）。
    重复触发（同规则同周期日已生成）返回 None，只推进不重记。
    推进始终从规则的 next_date 锚起算——手动提前记一期（fire_now 按 today 入账）
    不吃掉后续周期、不改变节奏。"""
    h = row_hash(rule["id"], period)
    if c.execute("SELECT 1 FROM transactions WHERE row_hash=?", (h,)).fetchone():
        # 该期已生成（上次触发后未推进的残局）：只推进不重记
        c.execute("UPDATE recurrences SET next_date=? WHERE id=?",
                  (next_period(rule["next_date"], rule["frequency"], rule["interval"],
                               rule["weekday"], rule["day_of_month"]), rule["id"]))
        return None
    cur = c.execute(
        """INSERT INTO transactions(ledger_id, type, amount, category_id, account_id,
           to_account_id, date, name, note, source, row_hash)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (rule["ledger_id"], rule["type"], rule["amount"], rule["category_id"],
         rule["account_id"], rule["to_account_id"], period, rule["name"],
         rule["note"], "recur", h))
    tx_id = cur.lastrowid
    c.execute(
        """UPDATE recurrences SET next_date=?, last_tx_id=?, last_run_date=? WHERE id=?""",
        (next_period(rule["next_date"], rule["frequency"], rule["interval"],
                     rule["weekday"], rule["day_of_month"]), tx_id, today, rule["id"]))
    return tx_id


def fire_rule(c: sqlite3.Connection, rule: sqlite3.Row, today: str) -> int | None:
    """触发单条到期规则：按 next_date 生成当期一笔流水再推进。返回流水 id（重复触发 None）。
    到期已过（next_date<today）也按 next_date 记账——那是周期的自然日期，漏跑不补、
    但这一期的账不能凭空记到别的日子。"""
    return _fire(c, rule, rule["next_date"], today)


def fire_now(c: sqlite3.Connection, rule: sqlite3.Row, today: str) -> int | None:
    """手动立即记一期（设置页「记一期」按钮）：到期未触发按 next_date，否则按今天记；
    节奏锚不变（推进始终从 next_date 起算，手动提前记不吃掉后续周期）。"""
    period = rule["next_date"] if rule["next_date"] <= today else today
    return _fire(c, rule, period, today)


def fire_due(c: sqlite3.Connection, today: str | None = None) -> list[int]:
    """扫描全部到期规则并逐条触发（每规则一笔，不回补）。返回新生成的流水 id。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    fired: list[int] = []
    with c:
        rules = c.execute(
            "SELECT * FROM recurrences WHERE enabled=1 AND next_date<=? "
            "ORDER BY next_date, id", (today,)).fetchall()
        for r in rules:
            try:
                tx = fire_rule(c, r, today)
                if tx:
                    fired.append(tx)
            except sqlite3.Error as e:   # 单规则失败不断整轮（下一 tick 重试）
                _log.warning(f"周期记账规则 {r['id']} 触发失败: {e}")
    if fired:
        _log.info(f"周期记账：生成 {len(fired)} 笔到期流水")
    return fired


def validate(frequency: str, interval: int, weekday, day_of_month) -> None:
    """创建/编辑时的规则校验（不合法抛 ValueError，server 端 400）。"""
    if frequency not in FREQUENCIES:
        raise ValueError(f"周期类型须为 {'/'.join(FREQUENCIES)}")
    n = int(interval) if interval is not None else 1
    if not 1 <= n <= _MAX_INTERVAL:
        raise ValueError(f"间隔须在 1-{_MAX_INTERVAL} 之间")
    if frequency == "weekly" and weekday is not None and not 0 <= int(weekday) <= 6:
        raise ValueError("weekday 须为 0（周一）-6（周日）")
    if frequency == "monthly" and day_of_month is not None and not 1 <= int(day_of_month) <= 31:
        raise ValueError("每月几号须为 1-31")


class RecurRunner:
    """常驻轮询线程（AutoBackup 同款）：60s 一 tick，每 tick 扫一轮到期规则。"""

    def __init__(self, on_change=None):
        self.on_change = on_change or (lambda: None)   # 生成流水后唤醒前端长轮询
        self.last_result: dict = {}

    def start(self) -> None:
        threading.Thread(target=self._loop, name="recurrence", daemon=True).start()

    def _loop(self) -> None:
        import db as dbm
        while True:
            try:
                fired = fire_due(dbm.connect())
                self.last_result = {"fired": len(fired)}
                if fired:
                    self.on_change()
            except Exception as e:   # 任何异常不中断循环
                self.last_result = {"error": str(e)[:300]}
            time.sleep(60)
