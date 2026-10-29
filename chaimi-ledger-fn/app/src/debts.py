"""债务台账：借出/借入登记 + 还款进度（纯独立记账，不碰流水——浩拍板）。

模型：debts（本金）+ debt_repays（还款子表，部分还款/多次还款）；
repaid 由 repays 实时汇总（不落列，防两处口径漂移），status 随汇总自动维护：
还清即 settled，删还款回 open。函数全部 conn 级纯操作，单测直驱。
"""
from __future__ import annotations

import datetime
import sqlite3

DIRECTIONS = ("out", "in")   # out=借出（别人欠我）in=借入（我欠别人）


def _touch_status(c: sqlite3.Connection, debt_id: int) -> None:
    """按 repays 汇总刷新 status：还清（累计>=本金）settled，否则 open。"""
    c.execute(
        """UPDATE debts SET status=CASE WHEN COALESCE((
             SELECT SUM(amount) FROM debt_repays WHERE debt_id=debts.id), 0) >= amount
           THEN 'settled' ELSE 'open' END WHERE id=?""", (debt_id,))


def debt_list(c: sqlite3.Connection, ledger_id: int, status: str = "all") -> list[dict]:
    """债务列表（repaid/remaining 实时汇总）；status 过滤 open/settled/all。"""
    where = "d.ledger_id=?"
    args: list = [ledger_id]
    if status in ("open", "settled"):
        where += " AND d.status=?"
        args.append(status)
    rows = c.execute(
        f"""SELECT d.*,
              COALESCE((SELECT SUM(amount) FROM debt_repays r WHERE r.debt_id=d.id), 0) AS repaid
            FROM debts d WHERE {where} ORDER BY d.date DESC, d.id DESC""", args).fetchall()
    return [dict(r) for r in rows]


def debt_summary(c: sqlite3.Connection, ledger_id: int) -> dict:
    """总借出/总借入在外（未还余额）+ 按人分组 + 逾期列表。"""
    # 在外余额 = Σ(本金 - 已还)，只算 open
    rows = c.execute(
        """SELECT d.direction, d.person, d.id,
              d.amount - COALESCE((SELECT SUM(amount) FROM debt_repays r
                                   WHERE r.debt_id=d.id), 0) AS remaining
            FROM debts d WHERE d.ledger_id=? AND d.status='open'""",
        (ledger_id,)).fetchall()
    out = {"total": 0, "count": 0}
    inn = {"total": 0, "count": 0}
    by_person: dict[tuple, dict] = {}
    overdue: list[dict] = []
    for r in rows:
        bucket = out if r["direction"] == "out" else inn
        bucket["total"] += r["remaining"]
        bucket["count"] += 1
        key = (r["direction"], r["person"])
        p = by_person.setdefault(key, {"direction": r["direction"], "person": r["person"],
                                       "remaining": 0, "count": 0})
        p["remaining"] += r["remaining"]
        p["count"] += 1
    # 逾期：约定还款日 < 今天 且未还清（due_date 可空不参与）
    today = datetime.date.today().isoformat()
    for d in c.execute(
            """SELECT d.*, COALESCE((SELECT SUM(amount) FROM debt_repays r
                                     WHERE r.debt_id=d.id), 0) AS repaid
               FROM debts d WHERE d.ledger_id=? AND d.status='open'
               AND d.due_date IS NOT NULL AND d.due_date<?""",
            (ledger_id, today)).fetchall():
        overdue.append(dict(d))
    return {
        "out": out,           # 总借出在外：别人欠我的
        "in": inn,            # 总借入在外：我欠别人的
        "by_person": sorted(by_person.values(),
                            key=lambda p: -p["remaining"]),
        "overdue": sorted(overdue, key=lambda d: d["due_date"]),
    }


def _check(c: sqlite3.Connection, ledger_id: int, direction: str,
           person: str, amount: int, date: str) -> None:
    if direction not in DIRECTIONS:
        raise ValueError("direction 须为 out（借出）/ in（借入）")
    if not (person or "").strip():
        raise ValueError("借出/入人不能为空")
    if int(amount) <= 0:
        raise ValueError("金额须大于 0")
    if not date:
        raise ValueError("借出/入时间不能为空")
    if not c.execute("SELECT 1 FROM ledgers WHERE id=?", (ledger_id,)).fetchone():
        raise ValueError("账本不存在")


def debt_add(c: sqlite3.Connection, ledger_id: int, direction: str, person: str,
             amount: int, date: str, due_date: str | None = None,
             note: str | None = None) -> int:
    with c:
        _check(c, ledger_id, direction, person, amount, date)
        cur = c.execute(
            "INSERT INTO debts(ledger_id, direction, person, amount, date, due_date, note) "
            "VALUES(?,?,?,?,?,?,?)",
            (ledger_id, direction, person.strip(), int(amount), date, due_date or None,
             (note or "").strip() or None))
    return cur.lastrowid


def debt_update(c: sqlite3.Connection, debt_id: int, fields: dict) -> bool:
    """字段级更新（person/amount/date/due_date/note/status）；status 手工干预后
    仍会被还款汇总刷新（增删还款即回归真实口径）。"""
    row = c.execute("SELECT id FROM debts WHERE id=?", (debt_id,)).fetchone()
    if not row:
        return False
    sets, args = [], []
    for col in ("direction", "person", "date", "status"):
        if col in fields and fields[col] is not None:
            if col == "direction" and fields[col] not in DIRECTIONS:
                raise ValueError("direction 须为 out / in")
            sets.append(f"{col}=?")
            args.append(str(fields[col]).strip())
    if fields.get("note") is not None:
        sets.append("note=?")
        args.append(str(fields["note"]).strip() or None)
    for col in ("amount",):
        if fields.get(col) is not None:
            if int(fields[col]) <= 0:
                raise ValueError("金额须大于 0")
            sets.append(f"{col}=?")
            args.append(int(fields[col]))
    if "due_date" in fields:
        sets.append("due_date=?")
        args.append(fields["due_date"] or None)
    if not sets:
        return True
    with c:
        c.execute(f"UPDATE debts SET {', '.join(sets)} WHERE id=?", (*args, debt_id))
        _touch_status(c, debt_id)
    return True


def debt_delete(c: sqlite3.Connection, debt_id: int) -> bool:
    with c:
        cur = c.execute("DELETE FROM debts WHERE id=?", (debt_id,))
        # repays 由外键 ON DELETE CASCADE 清（connect 已 PRAGMA foreign_keys=ON）
    return cur.rowcount > 0


def repay_add(c: sqlite3.Connection, debt_id: int, amount: int,
              date: str, note: str | None = None) -> int:
    with c:
        d = c.execute("SELECT amount FROM debts WHERE id=?", (debt_id,)).fetchone()
        if not d:
            raise ValueError("债务不存在")
        if int(amount) <= 0:
            raise ValueError("还款金额须大于 0")
        if not date:
            raise ValueError("还款时间不能为空")
        cur = c.execute(
            "INSERT INTO debt_repays(debt_id, amount, date, note) VALUES(?,?,?,?)",
            (debt_id, int(amount), date, (note or "").strip() or None))
        _touch_status(c, debt_id)
    return cur.lastrowid


def repay_delete(c: sqlite3.Connection, repay_id: int) -> bool:
    row = c.execute("SELECT debt_id FROM debt_repays WHERE id=?", (repay_id,)).fetchone()
    if not row:
        return False
    with c:
        c.execute("DELETE FROM debt_repays WHERE id=?", (repay_id,))
        _touch_status(c, row["debt_id"])
    return True


def repays_of(c: sqlite3.Connection, debt_id: int) -> list[dict]:
    return [dict(r) for r in c.execute(
        "SELECT * FROM debt_repays WHERE debt_id=? ORDER BY date DESC, id DESC", (debt_id,))]
