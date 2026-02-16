import json
import os

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

FILES = {
    "active_sources": "active_sources.json",
    "todo": "todo_data.json",
    "ignore_roles": "ignore_roles.json",
    "log_routes": "log_routes.json",
    "work_tasks": "work_tasks.json"
}

def _path(name):
    return os.path.join(DATA_DIR, FILES[name])

def load_json(name, default):
    try:
        with open(_path(name), "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json(name, data):
    with open(_path(name), "w") as f:
        json.dump(data, f)

