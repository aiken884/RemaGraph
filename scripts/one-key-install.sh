#!/bin/bash
# 一鍵安裝 RemaGraph（非技術使用者用）
# 使用方式： curl -sSL https://... | bash   （未來可放 raw）

echo "正在安裝 RemaGraph CLI ..."
if command -v uv &> /dev/null; then
  uv tool install git+https://github.com/aiken884/RemaGraph.git
else
  echo "請先安裝 uv：https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

echo ""
echo "✅ 安裝完成！"
echo "請執行： remagraph init"
echo "然後跟著指示設定環境變數即可開始使用。"
