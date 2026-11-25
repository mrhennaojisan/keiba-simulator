#!/usr/bin/env bash
# Lambda用のzip素材(build/lambda)を作る。Docker不要: arm64/Python3.12向けのwheelをpipで直接取得する。
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/lambda && mkdir -p build/lambda
pip install --quiet --upgrade --target build/lambda \
  --platform manylinux2014_aarch64 --implementation cp --python-version 3.12 --only-binary=:all: \
  numpy pandas requests beautifulsoup4
cp backend/*.py build/lambda/
# 容量削減（テスト・キャッシュ類を除く）
find build/lambda -type d \( -name "__pycache__" -o -name "tests" \) -prune -exec rm -rf {} + 2>/dev/null || true
echo "built: $(du -sh build/lambda | cut -f1)"
