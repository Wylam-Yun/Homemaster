#!/usr/bin/env bash
# HomeMaster 全量测试脚本

set -euo pipefail

echo "======================================"
echo "HomeMaster 全量测试"
echo "======================================"
echo ""

# 1. 运行完整的集成测试
echo "► 运行完整集成测试..."
if ./scripts/run_full_project_with_records.sh; then
    echo "✓ 集成测试通过"
else
    echo "✗ 集成测试失败"
    exit 1
fi

echo ""
echo "======================================"
echo "所有测试通过！"
echo "======================================"
echo ""
echo "可以使用以下命令运行其他测试："
echo ""
echo "# 仅运行API场景测试"
echo "./run_api_scenarios.sh"
echo ""
echo "# 测试单个场景"
echo "PYTHONPATH=src python -m task_brain.cli run \\"
echo "    --scenario check_medicine_success \\"
echo "    --instruction '去桌子那边看看药盒是不是还在。' \\"
echo "    --trace-jsonl /tmp/test.jsonl"
echo ""
echo "# 测试live API（需要有效的API key）"
echo "export NVIDIA_API_KEY='nvapi-...'"
echo "PYTHONPATH=src pytest tests/test_kimi_provider.py -m live_api -v"