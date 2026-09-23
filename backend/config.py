"""Read local configuration without executing .env content or logging secrets."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=ROOT / ".env", environ=None):
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.exists():
        return
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)", line)
        if not match:
            raise ValueError(f"Invalid .env syntax at line {number}; values are hidden.")
        key, value = match.groups()
        if key in values:
            raise ValueError(f"Duplicate .env variable at line {number}; values are hidden.")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Invalid .env quote at line {number}; values are hidden.")
            value = value[1:-1]
        values[key] = value
    for key, value in values.items():
        environ.setdefault(key, value)
