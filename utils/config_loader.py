"""加载 config.yaml，并将 ${VAR} 替换为环境变量（从 .env 文件或系统环境）。"""
import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

# 自动加载项目根目录的 .env 文件
_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=False)

_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _substitute(value: str) -> str:
    """将字符串中的 ${VAR} 替换为对应环境变量值。"""
    def replacer(m: re.Match) -> str:
        var = m.group(1)
        val = os.environ.get(var)
        if val is None:
            raise EnvironmentError(f"环境变量 '{var}' 未设置，请在 .env 文件中配置")
        return val
    return _VAR_RE.sub(replacer, value)


def _resolve(obj):
    """递归替换 dict/list/str 中的环境变量占位符。"""
    if isinstance(obj, dict):
        return {k: _resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve(v) for v in obj]
    if isinstance(obj, str):
        return _substitute(obj)
    return obj


def load_config(path: str | Path | None = None) -> dict:
    cfg_path = Path(path) if path else _ROOT / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return _resolve(raw)
