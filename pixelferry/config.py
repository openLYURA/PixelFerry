"""Configuration: load pixelferry.json with repo aliases."""

import os
import json
from typing import Dict, Optional


DEFAULT_CONFIG_NAME = "pixelferry.json"


def _find_config() -> Optional[str]:
    """Search for pixelferry.json in current dir and parent dirs."""
    d = os.getcwd()
    for _ in range(10):
        path = os.path.join(d, DEFAULT_CONFIG_NAME)
        if os.path.isfile(path):
            return path
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # Also check user home
    home_path = os.path.join(os.path.expanduser("~"), DEFAULT_CONFIG_NAME)
    if os.path.isfile(home_path):
        return home_path
    return None


def load_config(path: str = None) -> Dict:
    """Load config from file. Returns dict with 'repos' key.

    Config format:
    {
        "repos": {
            "alias": "/path/to/repo",
            ...
        }
    }
    """
    if path is None:
        path = _find_config()
    if path is None:
        return {"repos": {}}

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "repos" not in cfg:
        cfg["repos"] = {}
    return cfg


def resolve_repo(spec: str) -> str:
    """Resolve a repo spec to an absolute path.

    - If spec is a valid directory path, return it.
    - If spec matches a config alias, return the mapped path.
    - Otherwise raise ValueError.
    """
    # Direct path
    if os.path.isdir(spec):
        return os.path.abspath(spec)

    # Try as alias
    cfg = load_config()
    if spec in cfg["repos"]:
        p = os.path.expanduser(cfg["repos"][spec])
        if os.path.isdir(p):
            return os.path.abspath(p)
        raise ValueError(f"Alias '{spec}' maps to '{p}' which does not exist")

    raise ValueError(
        f"'{spec}' is not a valid directory or config alias.\n"
        f"Available aliases: {list(cfg['repos'].keys()) or '(none)'}"
    )


def save_config(cfg: Dict, path: str = None):
    """Save config to file.

    If no path is given, writes to the existing config location (found by
    searching upward from CWD), or to ~/pixelferry.json if no config exists yet.
    """
    if path is None:
        path = _find_config() or os.path.join(os.path.expanduser("~"), DEFAULT_CONFIG_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
