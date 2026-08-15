# SPDX-License-Identifier: Apache-2.0
"""Regression test: server 的 _RateLimiter buckets 不得無界成長。

修復前的行為（診斷確認）：_buckets 是 defaultdict，key 為呼叫端提供的任意
agent_id；過期 timestamp 只在同 key 再次 check() 時清理，key entry 本身
永不移除——長駐 serve 行程被大量不同 agent_id 呼叫時記憶體單調成長。
"""

from __future__ import annotations

import time

from remagraph.server import _RateLimiter


def test_stale_buckets_are_swept():
    limiter = _RateLimiter()
    stale = time.monotonic() - 3600  # 遠早於 rate limit window
    for i in range(700):
        limiter._buckets[f"stale-agent-{i}"] = [stale]

    limiter.check("fresh-agent")

    # 所有過期 key 應被清掉，不能只清當前 key
    assert len(limiter._buckets) < 700
    assert "fresh-agent" in limiter._buckets


def test_rate_limit_still_enforced_after_sweep():
    limiter = _RateLimiter()
    for _ in range(60):
        assert limiter.check("busy-agent")
    assert not limiter.check("busy-agent")
