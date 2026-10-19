"""安全/正确性修复回归（2026-10-13 审查）：
/api/config iLink 掩码、bot 撤销归属过滤、草稿入库失败回滚、iLink 媒体 URL 白名单
（防 SSRF）与日志脱敏、导入 IN 分块（超 SQLite 变量上限）、年份参数校验。"""
import sys
import unittest
from base import EnvCase, SRC

sys.path.insert(0, str(SRC))


class TestConfigMasking(EnvCase):
    """/api/config：iLink 凭证与 LLM Key / api_token 同口径掩码。"""

    def test_ilink_token_masked(self):
        import config
        import server
        cfg = config.load()
        cfg["llm"]["api_key"] = "sk-test"
        cfg["bot"]["api_token"] = "tok-test"
        cfg["bot"]["ilink"] = [{"account_id": "aid1", "token": "ilink-secret",
                                "base_url": "", "user": "u1", "ledger": 1, "label": ""}]
        config.save(cfg)
        out = server.get_config()
        self.assertEqual(out["llm"]["api_key"], "***")
        self.assertEqual(out["bot"]["api_token"], "***")
        self.assertEqual(out["bot"]["ilink"][0]["token"], "***")
        self.assertEqual(out["bot"]["ilink"][0]["account_id"], "aid1")   # 非敏感字段原样

    def test_ilink_token_masked_legacy_dict(self):
        """旧版单 dict 形态（config.DEFAULTS/随包 chaimi.conf 即此形态）同样掩码。"""
        import config
        import server
        cfg = config.load()
        cfg["bot"]["ilink"] = {"account_id": "x", "token": "real-secret",
                               "base_url": "", "user": "u", "ledger": 1, "label": "l"}
        config.save(cfg)
        out = server.get_config()
        self.assertEqual(out["bot"]["ilink"]["token"], "***")

    def test_web_auth_password_masked(self):
        import config
        import server
        cfg = config.load()
        cfg["web_auth"] = {"enabled": True, "username": "chaimi", "password": "pw-secret"}
        config.save(cfg)
        out = server.get_config()
        self.assertEqual(out["web_auth"]["password"], "***")
        self.assertEqual(out["web_auth"]["username"], "chaimi")

    def test_put_config_llm_key_whitelist(self):
        import config
        import server
        cfg = config.load()
        cfg["llm"]["model"] = "keep-me"
        config.save(cfg)
        server.put_config({"llm": {"model": "new-model", "rogue_key": "injected"}})
        out = config.load()
        self.assertEqual(out["llm"]["model"], "new-model")
        self.assertNotIn("rogue_key", out["llm"])   # 白名单外的键不落盘


class TestUndoScope(EnvCase):
    """撤销按归属过滤：A 通道只删本人（tags.attribution），网关/CLI 只删无归属的。"""

    def _ins(self, attribution):
        tags = {"attribution": attribution} if attribution else None
        return self.dbm.insert_tx(self.conn, self.lid, type="expense", amount=100,
                                  date="2026-10-13 12:00:00", name="测试",
                                  tags=tags, source="bot")

    def test_undo_respects_attribution(self):
        import bot_core
        other = self._ins("老公")     # 家人 A 记的
        mine = self._ins("")          # 网关记的（无归属）
        bot_core._undo(self.lid, "")  # 网关侧撤销：不得动 A 的
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM transactions WHERE id=?", (mine,)).fetchone())
        self.assertIsNotNone(self.conn.execute(
            "SELECT id FROM transactions WHERE id=?", (other,)).fetchone())

    def test_undo_with_attribution_only_own(self):
        import bot_core
        a = self._ins("老公")
        b = self._ins("老婆")
        bot_core._undo(self.lid, "老婆")
        self.assertIsNone(self.conn.execute(
            "SELECT id FROM transactions WHERE id=?", (b,)).fetchone())
        self.assertIsNotNone(self.conn.execute(
            "SELECT id FROM transactions WHERE id=?", (a,)).fetchone())


class TestConfirmRollback(EnvCase):
    """草稿入库失败：DB 事务回滚 + 草稿放回会话（小票引用不丢，可重试）。"""

    def test_draft_restored_on_failure(self):
        from unittest import mock
        import bot_core
        s = {"draft": {"items": [{"type": "expense", "amount": 100, "name": "早餐"}],
                       "text": "早餐10块"}}
        with mock.patch.object(bot_core.dbm, "insert_tx",
                               side_effect=Exception("disk full")):
            reply = bot_core._confirm(s, self.lid)
        self.assertIn("入库失败", reply)
        self.assertIsNotNone(s["draft"])                    # 草稿回来了
        self.assertEqual(s["draft"]["items"][0]["name"], "早餐")


class TestIlinkMediaGuard(unittest.TestCase):
    """媒体 URL 白名单（防入站消息借字段盲打内网 SSRF）与日志脱敏。"""

    def setUp(self):
        import bot_ilink
        self.bot_ilink = bot_ilink
        self.bot = bot_ilink.ILinkBot()

    def test_private_url_rejected(self):
        for url in ("http://192.168.1.1/x.jpg", "https://10.0.0.8/a.png",
                    "http://169.254.169.254/latest/meta-data", "https://evil.com/x.jpg"):
            self.assertEqual(self.bot._media_url({"cdn_url": url}), "")

    def test_malformed_url_no_raise(self):
        """畸形 URL（非法 IPv6 括号等）按拒收处理，不得抛异常炸掉轮询线程。"""
        for url in ("http://[", "http://[[[[", "https://]"):
            self.assertEqual(self.bot._media_url({"cdn_url": url}), "")

    def test_wx_host_ok_matrix(self):
        ok = self.bot_ilink._wx_host_ok
        self.assertTrue(ok("https://novac2c.cdn.weixin.qq.com/c2c/download?a=1"))
        self.assertTrue(ok("https://weixin.qq.com/x"))
        self.assertFalse(ok("http://192.168.1.1/x.jpg"))
        self.assertFalse(ok("https://evilweixin.qq.com/x"))       # 前缀仿冒
        self.assertFalse(ok("https://weixin.qq.com.evil.com/x"))  # 后缀仿冒
        self.assertFalse(ok("http://["))                          # 畸形
        self.assertFalse(ok(""))

    def test_redirect_gate_blocks_non_wx_target(self):
        """3xx 逐跳校验：重定向目标不在微信白名单 → 拒绝（防白名单域开放重定向跳内网）。"""
        import urllib.error
        h = self.bot_ilink._MediaRedirectHandler()
        for bad in ("http://169.254.169.254/latest/meta-data", "http://10.0.0.5/x",
                    "http://[", "https://evil.com/a"):
            with self.assertRaises(urllib.error.URLError):
                h.redirect_request(None, None, 302, "Found", {}, bad)

    def test_weixin_cdn_allowed(self):
        url = "https://novac2c.cdn.weixin.qq.com/c2c/download?fid=abc"
        self.assertEqual(self.bot._media_url({"cdnurl": url}), url)

    def test_encrypt_param_quoted_to_cdn(self):
        out = self.bot._media_url({"encrypt_query_param": "a=b&c=d"})
        self.assertTrue(out.startswith(self.bot_ilink.CDN_BASE + "/download?"))
        self.assertIn("a%3Db%26c%3Dd", out)   # 值整体编码，& 不夹带额外参数

    def test_filekey_fallback(self):
        out = self.bot._media_url({"filekey": "k1"})
        self.assertEqual(out, f"{self.bot_ilink.CDN_BASE}/download?filekey=k1")

    def test_redact_masks_credentials(self):
        r = self.bot_ilink._redact({
            "status": "confirmed", "bot_token": "secret", "ret": 0,
            "msgs": [{"context_token": "ctx", "text": "你好"}],
            "aeskey": "0123456789abcdef0123456789abcdef"})
        self.assertEqual(r["bot_token"], "***")
        self.assertEqual(r["msgs"][0]["context_token"], "***")
        self.assertEqual(r["aeskey"], "***")
        self.assertEqual(r["status"], "confirmed")           # 非敏感原样
        self.assertEqual(r["msgs"][0]["text"], "你好")


class TestImportChunking(EnvCase):
    """IN (...) 分块：超过 SQLite 单语句变量上限（999/32766）的批次照常撤销。"""

    def test_undo_batch_over_var_limit(self):
        import import_bill as ib
        cur = self.conn.execute(
            "INSERT INTO import_batches(ledger_id, source, filename, count, imported_at) "
            "VALUES(?,?,?,?,datetime('now'))", (self.lid, "alipay", "big.csv", 0))
        bid = cur.lastrowid
        rows = [(self.lid, 100, "2026-10-13 00:00:00", bid)] * 1200
        with self.conn:
            self.conn.executemany(
                "INSERT INTO transactions(ledger_id, type, amount, date, batch_id) "
                "VALUES(?, 'expense', ?, ?, ?)", rows)
        n = ib.undo_batch(self.conn, bid)
        self.assertEqual(n, 1200)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE batch_id=?", (bid,)).fetchone()[0], 0)


class TestYearValidation(unittest.TestCase):
    """stats_monthly 非数字年份 → 400（不再 500）。"""

    def test_bad_year_400(self):
        from fastapi import HTTPException
        import server
        with self.assertRaises(HTTPException) as cm:
            server.stats_monthly(1, "20x6")
        self.assertEqual(cm.exception.status_code, 400)


class TestLockRegistration(unittest.TestCase):
    """鉴权先于锁注册：未授权 id 不在白名单 → 拒绝且不驻留 per-user 锁（防锁表被陌生 id 撑大）。"""

    def test_unauthorized_user_registers_no_lock(self):
        import bot_core
        reply = bot_core.handle("hi", "stranger_x")   # 不在白名单（默认仅 gateway）
        self.assertIn("未绑定", reply)
        self.assertNotIn("stranger_x", bot_core._user_locks)
