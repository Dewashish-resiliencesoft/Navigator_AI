"""CLI: python -m navigator.agent.eval <cases.yaml> <product_id>"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from navigator.agent.eval.runner import load_cases


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: python -m navigator.agent.eval <cases.yaml> <product_id>")
        return 2
    cases = load_cases(Path(sys.argv[1]))
    print(json.dumps({"cases": len(cases), "product_id": sys.argv[2]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
