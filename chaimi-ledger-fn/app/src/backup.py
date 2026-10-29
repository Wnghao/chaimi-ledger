"""备份/恢复：sqlite3 backup API（WAL 下不能直接拷文件，DESIGN 决策4）+ 配置副本。

自动备份分层（祖父-父-子简化版）：
- 月备：每月最后一天生成 chaimi-ledger-YYYYMM.db（同月重复执行则覆盖）；
- 年备：每年 12 月 31 日生成 chaimi-ledger-YYYY.db（当天年备优先，跳过月备）；
- 保留策略只清理上述周期文件（按文件名位数识别），手动备份不受影响。

WebDAV 备份（2026-10-29，可选）：备份文件（.db + 同名副文件）上传到用户自己的
WebDAV 服务（坚果云/群晖/Alist 等）；urllib 直连零新依赖，Basic Auth。
"""
from __future__ import annotations

import base64
import calendar
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import config
import db
import applog

_log = applog.get("backup")

_SIDE_SUFFIXES = (".conf.json", ".receipts.zip")

def _valid_name(name: str) -> bool:
    """备份文件名安全校验（restore/delete/download 共用）：
    chaimi- 前缀白名单 + .db 后缀限定——副文件（.conf.json 含明文 API Key）
    与任意改名文件一律不可经这三个入口触达（副文件只随主文件联动，见 _SIDE_SUFFIXES）。"""
    return (name.startswith("chaimi-") and name.endswith(".db")
            and "/" not in name and "\\" not in name and ".." not in name)

def _locate(name: str):
    """校验文件名安全并定位备份主文件（restore/delete/download 共用）；不存在抛 FileNotFoundError。"""
    if not _valid_name(name):
        raise ValueError("非法备份文件名")
    src_file = config.backup_dir() / name
    if not src_file.exists():
        raise FileNotFoundError("备份不存在")
    return src_file

def create_backup(period: str = "") -> dict:
    """period: ""=手动(完整时间戳) "monthly"=YYYYMM "yearly"=YYYY。
    文件名统一 chaimi-ledger-<时间戳>.db/.conf.json，手动与自动仅时间戳粒度不同。
    小票一并备份：打包为同名 .receipts.zip（目录为空则不生成）。"""
    bdir = config.backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    if period == "monthly":
        stamp = now.strftime("%Y%m")
    elif period == "yearly":
        stamp = now.strftime("%Y")
    else:
        stamp = now.strftime("%Y%m%d_%H%M%S")
    dest = bdir / f"chaimi-ledger-{stamp}.db"
    src = sqlite3.connect(config.db_path())
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    # 配置副本
    conf = config.conf_path()
    if conf.exists():
        (bdir / f"chaimi-ledger-{stamp}.conf.json").write_bytes(conf.read_bytes())
    rdir = config.receipts_dir()
    rfiles = [f for f in rdir.iterdir() if f.is_file()] if rdir.exists() else []
    if rfiles:
        with zipfile.ZipFile(bdir / f"chaimi-ledger-{stamp}.receipts.zip", "w", zipfile.ZIP_STORED) as z:
            for f in rfiles:
                z.write(f, f.name)
    return {"file": str(dest), "size": dest.stat().st_size}

def list_backups() -> list[dict]:
    bdir = config.backup_dir()
    out = []
    if bdir.exists():
        for f in sorted(bdir.glob("chaimi-*.db"), reverse=True):
            out.append({"file": f.name, "size": f.stat().st_size,
                        "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")})
    return out

def restore_backup(name: str) -> None:
    """恢复：校验文件名安全 + integrity_check 通过才覆盖。"""
    src_file = _locate(name)
    # 先校验备份完整性；非 SQLite 文件统一转 ValueError（server 映射 400，不再裸 500）
    chk = sqlite3.connect(src_file)
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError as e:
        raise ValueError(f"备份文件不是有效的 SQLite 数据库: {e}") from e
    finally:
        chk.close()
    if not ok:
        raise ValueError("备份文件完整性校验失败")
    # 旧实现"临时文件替换 db"在 Linux 下各线程缓存的旧连接仍读旧 inode（恢复"看似没生效"的根因），
    # 在 Windows 下直接被旧句柄锁死无法替换；backup 写同一文件，所有连接立即可见
    target = config.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(src_file)
    dst = sqlite3.connect(target, timeout=10)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    db.reset_connections()   # 防御：作废线程缓存连接（清各自的 schema/page 缓存）
    # 配置副本一并恢复（AI API Key 等随备份回到备份时点）
    conf_backup = src_file.with_suffix(".conf.json")
    if conf_backup.exists():
        try:
            config.conf_path().write_bytes(conf_backup.read_bytes())
        except OSError:
            pass
    receipts_backup = config.backup_dir() / (src_file.stem + ".receipts.zip")
    if receipts_backup.exists():
        try:
            rdir = config.receipts_dir()
            rdir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(receipts_backup) as z:
                for n in z.namelist():
                    if "/" not in n and ".." not in n:   # 只收平铺文件名，防路径逃逸
                        z.extract(n, rdir)
        except OSError:
            pass

def delete_backup(name: str) -> None:
    """删除备份（.db 与同名副文件一起删）。"""
    src_file = _locate(name)
    src_file.unlink()
    for suffix in _SIDE_SUFFIXES:
        src_file.with_suffix(suffix).unlink(missing_ok=True)

_MONTH_FILE = re.compile(r"^chaimi-ledger-(\d{6})\.db$")
_YEAR_FILE = re.compile(r"^chaimi-ledger-(\d{4})\.db$")

def _cleanup_old(keep: int, now: datetime, *, yearly: bool) -> None:
    """周期备份按保留数清理（月备按月数、年备按年数）：
    删除过期的主文件与全部同名副文件（.conf.json/.receipts.zip，两侧同一口径）。"""
    if keep <= 0:
        return
    pat = _YEAR_FILE if yearly else _MONTH_FILE
    for f in config.backup_dir().glob("chaimi-ledger-*.db"):
        m = pat.match(f.name)
        if not m:
            continue
        n = int(m.group(1))
        age = (now.year - n) if yearly else (now.year * 12 + now.month) - (n // 100 * 12 + n % 100)
        if age >= keep:
            f.unlink(missing_ok=True)
            for suffix in _SIDE_SUFFIXES:
                f.with_suffix(suffix).unlink(missing_ok=True)

def _is_last_day_of_month(now: datetime) -> bool:
    return now.day == calendar.monthrange(now.year, now.month)[1]

# ---------- WebDAV 备份（可选；urllib 直连，零新依赖） ----------

def _webdav_cfg() -> dict:
    return config.load().get("webdav") or {}

def _dav_url(cfg: dict, *parts: str) -> str:
    base = (cfg.get("url") or "").rstrip("/")
    seg = "/".join(p.strip("/") for p in parts if p)
    return f"{base}/{seg}" if seg else base

def _dav_open(method: str, url: str, cfg: dict, data: bytes | None = None,
              headers: dict | None = None, timeout: int = 30):
    req = urllib.request.Request(url, data=data, method=method)
    token = base64.b64encode(f"{cfg.get('username', '')}:{cfg.get('password', '')}".encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=timeout)

def _dav_check(cfg: dict) -> None:
    if not (cfg.get("url") or "").startswith(("http://", "https://")):
        raise ValueError("WebDAV 地址须以 http(s):// 开头")
    if not cfg.get("username") or not cfg.get("password"):
        raise ValueError("WebDAV 用户名/密码不能为空")

def _dav_mkdir(cfg: dict) -> None:
    """远端目录不存在则创建（已存在视为成功）。"""
    durl = _dav_url(cfg, cfg.get("dir") or "")
    try:
        with _dav_open("PROPFIND", durl, cfg, headers={"Depth": "0"}) as r:
            r.read()
        return   # 目录已在
    except urllib.error.HTTPError as e:
        if e.code not in (404, 409):
            raise ValueError(f"WebDAV 目录探测失败：HTTP {e.code}") from e
    try:
        with _dav_open("MKCOL", durl, cfg) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code not in (405, 301, 409):   # 405=已存在（竞态），容忍
            raise ValueError(f"WebDAV 目录创建失败：HTTP {e.code}") from e

def webdav_test() -> dict:
    """连通性测试：OPTIONS 根地址 → 目录探测/创建。"""
    cfg = _webdav_cfg()
    _dav_check(cfg)
    try:
        with _dav_open("OPTIONS", _dav_url(cfg), cfg) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"WebDAV 连接失败：HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"WebDAV 连接失败：{e}") from e
    created = False
    durl = _dav_url(cfg, cfg.get("dir") or "")
    try:
        with _dav_open("PROPFIND", durl, cfg, headers={"Depth": "0"}) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code not in (404, 409):
            raise ValueError(f"WebDAV 目录探测失败：HTTP {e.code}") from e
        try:
            with _dav_open("MKCOL", durl, cfg) as r:
                r.read()
            created = True
        except urllib.error.HTTPError as e2:
            if e2.code not in (405, 301, 409):
                raise ValueError(f"WebDAV 目录创建失败：HTTP {e2.code}") from e2
    return {"ok": True, "dir_created": created}

def webdav_upload(name: str) -> dict:
    """上传指定备份及全部同名副文件（.conf.json/.receipts.zip）；远端目录自动创建。"""
    cfg = _webdav_cfg()
    _dav_check(cfg)
    src = _locate(name)
    _dav_mkdir(cfg)
    uploaded = []
    for f in [src] + [p for p in sorted(src.parent.glob(src.stem + ".*")) if p.is_file()]:
        data = f.read_bytes()
        with _dav_open("PUT", _dav_url(cfg, cfg.get("dir") or "", f.name), cfg, data=data) as r:
            r.read()
        uploaded.append({"file": f.name, "size": len(data)})
    return {"ok": True, "uploaded": uploaded}

def webdav_list() -> list[dict]:
    """远端 .db 备份列表（PROPFIND Depth 1，宽松解析命名空间各异的响应）。"""
    cfg = _webdav_cfg()
    _dav_check(cfg)
    try:
        with _dav_open("PROPFIND", _dav_url(cfg, cfg.get("dir") or ""), cfg,
                       headers={"Depth": "1"}) as r:
            xml = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []   # 目录还没建过：等价于空列表
        raise ValueError(f"WebDAV 列表失败：HTTP {e.code}") from e
    out = []
    for m in re.finditer(r"<(?:[\w.]+:)?response>(.*?)</(?:[\w.]+:)?response>", xml, re.S | re.I):
        block = m.group(1)
        hm = re.search(r"<(?:[\w.]+:)?href>(.*?)</(?:[\w.]+:)?href>", block, re.S | re.I)
        if not hm:
            continue
        fname = unquote(hm.group(1)).rstrip("/").rsplit("/", 1)[-1]
        if not fname.endswith(".db"):
            continue
        sm = re.search(r"<(?:[\w.]+:)?getcontentlength>(\d+)<", block, re.I)
        out.append({"file": fname, "size": int(sm.group(1)) if sm else 0})
    return sorted(out, key=lambda x: x["file"], reverse=True)

def webdav_download(name: str) -> dict:
    """从远端下载备份到本地备份目录（integrity_check 通过才落盘），随后可走常规恢复。"""
    cfg = _webdav_cfg()
    _dav_check(cfg)
    if not _valid_name(name):
        raise ValueError("非法备份文件名")
    try:
        with _dav_open("GET", _dav_url(cfg, cfg.get("dir") or "", name), cfg) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"下载失败：HTTP {e.code}") from e
    dest = config.backup_dir() / name
    dest.write_bytes(data)
    conf_remote = Path(name).with_suffix(".conf.json").name
    try:
        with _dav_open("GET", _dav_url(cfg, cfg.get("dir") or "", conf_remote), cfg) as r:
            (config.backup_dir() / conf_remote).write_bytes(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        pass   # 配置副本缺失可容忍
    chk = sqlite3.connect(dest)
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        chk.close()
    if not ok:
        dest.unlink(missing_ok=True)
        raise ValueError("下载的备份完整性校验失败")
    return {"ok": True, "file": name, "size": len(data)}

class AutoBackup:
    """自动备份：到配置 hour 点检查——年末(12-31)优先生成年备并跳过月备；
    其余月末生成本月月备。保留按月数/年数分层清理，只清周期文件。
    每轮重读配置，设置页改参数即时生效。"""

    def __init__(self):
        self._last_date = ""
        self.last_result: dict = {"error": "尚未备份"}

    def start(self) -> None:
        threading.Thread(target=self._loop, name="autobackup", daemon=True).start()

    def _loop(self) -> None:
        while True:
            cfg = config.load().get("backup", {})
            try:
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                if (cfg.get("enabled") and int(cfg.get("hour", 3)) == now.hour
                        and self._last_date != today
                        and _is_last_day_of_month(now)):
                    self._last_date = today
                    if now.month == 12 and now.day == 31:
                        # 12-31 年备优先（年备已覆盖当月），跳过月备
                        if int(cfg.get("keep_years", 3)) > 0:
                            self.last_result = create_backup("yearly")
                            _cleanup_old(int(cfg.get("keep_years", 3)), now, yearly=True)
                            self._webdav_auto()
                    elif int(cfg.get("keep_months", 3)) > 0:
                        self.last_result = create_backup("monthly")
                        _cleanup_old(int(cfg.get("keep_months", 3)), now, yearly=False)
                        self._webdav_auto()
            except Exception as e:  # 备份失败不中断循环
                self.last_result = {"error": str(e)[:300]}
            time.sleep(60)

    def _webdav_auto(self) -> None:
        """自动备份生成后按配置同步上传 WebDAV（auto 开关；失败只记日志不断备份链路）。"""
        wc = config.load().get("webdav") or {}
        if not (wc.get("auto") and wc.get("url")):
            return
        fname = os.path.basename(self.last_result.get("file", ""))
        try:
            up = webdav_upload(fname)
            self.last_result["webdav"] = f"已上传 {len(up['uploaded'])} 个文件到 WebDAV"
            _log.info(f"WebDAV 同步：{fname}")
        except Exception as e:
            self.last_result["webdav_error"] = str(e)[:200]
            _log.warning(f"WebDAV 上传失败：{e}")
