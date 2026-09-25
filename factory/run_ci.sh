#!/usr/bin/env bash
set -euo pipefail

if [[ -z "$(find . -name 'test_*.py' -not -path './.git/*' -not -path './.asd-factory/*' -print -quit)" ]]; then
  echo "No pytest files found; CI no-op pass"
  exit 0
fi

python -m pip install pytest -q
python -m pytest
