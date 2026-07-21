# WU-9 CI / 冒煙 / mutmut
repo RemaGraph.

1. 確認 .github/workflows/test.yml：smoke → lint → full test；smoke 跑 tests/smoke 且用 env 隔離 state
2. 確認 gitleaks / pip-audit workflow 合理
3. 新增或調整 mutmut 設定：針對 arbitration + dedup，記錄如何跑；若 CI 過慢可獨立 workflow 非 blocking 但要有文件
4. 更新 docs/governance/checklist.md P0-3 相關可勾項
5. 跑 pytest smoke + full 確保綠

結尾 ## WU-9 DONE
