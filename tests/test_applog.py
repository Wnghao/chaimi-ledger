"""applog 日志模块：初始化幂等、轮转文件落盘、子 logger 命名。"""
import logging
import sys
import unittest
from base import SRC

sys.path.insert(0, str(SRC))
import applog
import config


class TestApplog(unittest.TestCase):
    def test_setup_idempotent_and_writes_file(self):
        import os, tempfile, pathlib
        tmp = tempfile.mkdtemp()
        os.environ["LULU_CONF"] = str(pathlib.Path(tmp) / "c.conf")
        os.environ["LULU_DB"] = str(pathlib.Path(tmp) / "t.db")
        log1 = applog.setup()
        log2 = applog.setup()                      # 幂等：不重复挂 handler
        self.assertIs(log1, log2)
        self.assertEqual(len(log1.handlers), 2)    # stdout + 轮转文件
        name = applog.get("utest").name
        self.assertEqual(name, "lulu.utest")
        applog.get("utest").info("冒烟日志行-for-test")
        for h in log1.handlers:
            h.flush()
        content = config.log_path().read_text(encoding="utf-8")
        self.assertIn("冒烟日志行-for-test", content)   # 轮转文件确实落盘
        self.assertIn("INFO [lulu.utest]", content)     # 格式带级别与模块名
        # 清理：摘掉 handler 关文件句柄（Windows 删除前置条件）
        for h in list(log1.handlers):
            h.close()
            log1.removeHandler(h)

    def test_rotating_file_config(self):
        import logging.handlers
        log = applog.setup()
        fh = [h for h in log.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        self.assertEqual(len(fh), 1)
        self.assertEqual(fh[0].maxBytes, 2_000_000)     # 2MB × backupCount=2（评估 R-09）


if __name__ == "__main__":
    unittest.main()
