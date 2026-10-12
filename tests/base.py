"""测试公共基类：每个用例独立临时目录（LULU_DB/LULU_CONF 指过去），互不污染。

运行：项目根目录 `python -m unittest discover -s tests -v`（build.bat 已串联）。
"""
import importlib
import os
import pathlib
import sys
import tempfile
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "lulu-ledger-fn" / "app" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class EnvCase(unittest.TestCase):
    """隔离环境的用例基类：setUp 建临时库并 init_db，tearDown 清目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["LULU_CONF"] = str(pathlib.Path(self._tmp.name) / "lulu.conf")
        os.environ["LULU_DB"] = str(pathlib.Path(self._tmp.name) / "lulu.db")
        import db
        importlib.reload(db)
        db.reset_connections()
        db.init_db()
        self.dbm = db
        self.conn = db.connect()
        self.lid = self.conn.execute("SELECT id FROM ledgers").fetchone()["id"]

    def tearDown(self):
        import db
        conn = getattr(db._local, "conn", None)   # Windows：显式关连接才能删临时库文件
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            db._local.conn = None
        db.reset_connections()
        self._tmp.cleanup()

    def topCat(self, name, ftype="expense"):
        return self.dbm.top_cat_id(self.conn, self.lid, name, ftype)
