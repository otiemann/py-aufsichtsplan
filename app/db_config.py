from __future__ import annotations

import json
import os
import sys
from typing import List, Optional

DB_CONFIG_FILENAME = "database_config.json"
DB_PATH_KEY = "database_path"


def get_app_data_dir() -> str:
    data_dir = os.environ.get("APP_DATA_DIR")
    if data_dir:
        path = os.path.abspath(os.path.expanduser(os.path.expandvars(data_dir)))
    elif getattr(sys, "frozen", False):
        path = os.path.dirname(os.path.abspath(sys.executable))
    else:
        path = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(path, exist_ok=True)
    return path


def get_database_config_path(data_dir: Optional[str] = None) -> str:
    root = data_dir or get_app_data_dir()
    return os.path.join(root, DB_CONFIG_FILENAME)


def normalize_database_path(raw_path: str, data_dir: Optional[str] = None) -> str:
    value = (raw_path or "").strip()
    if not value:
        raise ValueError("Datenbankpfad darf nicht leer sein.")
    expanded = os.path.expanduser(os.path.expandvars(value))
    if not os.path.isabs(expanded):
        base = data_dir or get_app_data_dir()
        expanded = os.path.join(base, expanded)
    return os.path.abspath(expanded)


def read_database_path_config(data_dir: Optional[str] = None) -> Optional[str]:
    config_path = get_database_config_path(data_dir)
    if not os.path.exists(config_path):
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        raw_path = payload.get(DB_PATH_KEY, "")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        return normalize_database_path(raw_path, data_dir)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def write_database_path_config(raw_path: str, data_dir: Optional[str] = None) -> str:
    normalized = normalize_database_path(raw_path, data_dir)
    config_path = get_database_config_path(data_dir)
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    payload = {DB_PATH_KEY: normalized}
    tmp_path = config_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
    os.replace(tmp_path, config_path)
    return normalized


def clear_database_path_config(data_dir: Optional[str] = None) -> None:
    config_path = get_database_config_path(data_dir)
    try:
        os.remove(config_path)
    except FileNotFoundError:
        return


def list_database_candidates(data_dir: Optional[str] = None) -> List[str]:
    candidates = set()
    base_dirs = {
        get_app_data_dir(),
        os.getcwd(),
        os.path.join(os.getcwd(), "app"),
    }
    if data_dir:
        base_dirs.add(os.path.abspath(data_dir))

    known_names = {
        "aufsichtsplan.db",
        "aufsichtSplan.db",
        "vertretungsplan.db",
        "user_db_copy.db",
    }

    configured = read_database_path_config(data_dir)
    if configured:
        candidates.add(configured)

    env_path = os.environ.get("DATABASE_PATH")
    if env_path:
        try:
            candidates.add(normalize_database_path(env_path, data_dir))
        except ValueError:
            pass

    for folder in base_dirs:
        if not os.path.isdir(folder):
            continue
        for name in known_names:
            path = os.path.abspath(os.path.join(folder, name))
            if os.path.exists(path):
                candidates.add(path)
        try:
            for entry in os.listdir(folder):
                if entry.lower().endswith(".db"):
                    path = os.path.abspath(os.path.join(folder, entry))
                    if os.path.isfile(path):
                        candidates.add(path)
        except OSError:
            continue

    return sorted(candidates)
