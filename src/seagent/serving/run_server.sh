#!/usr/bin/env bash
# 启动线上客服服务(uvicorn)。
#
# 用法:
#   bash src/seagent/serving/run_server.sh            # 默认 0.0.0.0:8080
#   HOST=127.0.0.1 PORT=9000 bash .../run_server.sh   # 自定义
#
# 依赖(可选): pip install 'fastapi>=0.110' 'uvicorn[standard]'
# 核心业务逻辑(schema/session/feedback)不依赖这两者。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 项目根 = serving/../../../..  ->  .../self_evolving_agent
ROOT="$(cd "${HERE}/../../.." && pwd)"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"

export PYTHONPATH="${ROOT}/src:${PYTHONPATH:-}"

if ! python -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "[run_server] FastAPI/uvicorn 未安装。请先:" >&2
  echo "  pip install 'fastapi>=0.110' 'uvicorn[standard]'" >&2
  exit 1
fi

# 通过一个轻量入口模块装配 app。这里用环境变量 SEAGENT_APP 指向工厂，
# 默认用 demo 工厂(MockBackend)，生产替换成你自己的装配模块即可。
APP_FACTORY="${SEAGENT_APP:-seagent.serving._demo_app:app}"

echo "[run_server] starting uvicorn on ${HOST}:${PORT}  (app=${APP_FACTORY})"
exec python -m uvicorn "${APP_FACTORY}" --host "${HOST}" --port "${PORT}"
