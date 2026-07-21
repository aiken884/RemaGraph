"""CI gate：確認 SQLite ≥ 3.38 且 FTS5 trigram 可用。

此檔案由 CI 獨立執行（`python ci/test_trigram_gate.py`），
不依賴 remagraph 套件本身。
"""

import sqlite3


def test_fts5_trigram_available() -> None:
    """確認 FTS5 trigram tokenizer 可用且支援子字串匹配。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content, tokenize='trigram')")
    conn.execute("INSERT INTO t VALUES ('hello world test')")
    rows = conn.execute("SELECT * FROM t WHERE t MATCH 'ell'").fetchall()
    assert len(rows) == 1, f"trigram 應匹配子字串 'ell'，實際回傳 {len(rows)} 筆"
    conn.close()


def test_fts5_trigram_rejects_bigram() -> None:
    """確認 bigram 查詢不被 trigram tokenizer 匹配。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content, tokenize='trigram')")
    conn.execute("INSERT INTO t VALUES ('測試中文 trigram')")
    rows = conn.execute("SELECT * FROM t WHERE t MATCH 'gl'").fetchall()
    assert len(rows) == 0, f"bigram 'gl' 不應被 trigram tokenizer 匹配，實際回傳 {len(rows)} 筆"
    conn.close()


def test_sqlite_version() -> None:
    """確認 SQLite 版本 ≥ 3.38。"""
    v = sqlite3.sqlite_version_info
    assert v >= (3, 38, 0), f"SQLite 版本 {v} < 3.38，需升級"


if __name__ == "__main__":
    test_sqlite_version()
    print(f"SQLite {sqlite3.sqlite_version_info} >= 3.38 OK")

    test_fts5_trigram_available()
    print("trigram 子字串匹配 OK")

    test_fts5_trigram_rejects_bigram()
    print("trigram 拒絕 bigram OK")

    print("\nAll trigram CI gates passed.")
