#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
if [ ! -f .env ]; then
    echo "Сначала запустите setup_env.sh."
    exit 1
fi
PY=python
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" bot.py
