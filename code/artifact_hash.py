# -*- coding: utf-8 -*-
"""
code/artifact_hash.py —— 跨平台 Artifact 哈希（V0.3.1.2 P0 · 封板补丁）
======================================================================

问题
----
demo_artifacts 的文本文件（manifest.json / metadata.json / cases_demo.json /
predictions_demo.csv）在 Windows 上以 CRLF 写出；Linux / clean clone 上是 LF
（配合 .gitattributes `text eol=lf` 强制 LF）。直接对磁盘字节做 SHA-256，
CRLF 与 LF 两个 checkout 会产生不同哈希 → clean clone 的 manifest 校验 FAIL。

方案（canonical 归一化）
----------------------
  * 文本文件（.csv/.json/.md/.txt/.yml/.yaml/.py）：先做 CRLF→LF 归一化，再 SHA-256。
      → 无论磁盘是 CRLF 还是 LF，哈希一致（跨平台可复现）。
  * 二进制文件（.parquet/.xlsx 等）：字节原样哈希（不归一化）。
  * manifest 声明 hash_algorithm="sha256"、hash_normalization="canonical-text"；
    写入端与校验端共用本模块 → 规则单一实现，不漂移。

用法
----
    from code.artifact_hash import canonical_sha256
    h = canonical_sha256("demo_artifacts/manifest.json")   # 与平台无关

由 build_demo_artifacts.py（写入）、code/tests/test_demo_artifacts.py 与
code/tests/test_v0312_freeze.py（校验）共用。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: 视为"文本"参与 CRLF→LF 归一化的后缀（其余按二进制原样哈希）
TEXT_EXTS: frozenset = frozenset({".py", ".json", ".csv", ".md", ".txt", ".yml", ".yaml"})

#: manifest 中声明的算法 / 归一化（单一事实来源，写入端必须与之一致）
HASH_ALGORITHM = "sha256"
HASH_NORMALIZATION = "canonical-text"


def is_text_file(name: str) -> bool:
    """按后缀判断是否属于"文本文件"（参与 CRLF→LF 归一化）。"""
    return Path(name).suffix.lower() in TEXT_EXTS


def canonical_sha256(path) -> str:
    """跨平台 SHA-256：文本文件 CRLF→LF 归一化后哈希；二进制原样。

    无论磁盘是 CRLF 还是 LF，文本文件的 canonical 哈希一致；
    二进制文件字节哈希不受换行影响，原样即可。
    """
    p = Path(path)
    data = p.read_bytes()
    if is_text_file(p.name):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()
