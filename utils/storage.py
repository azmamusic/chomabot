import json
import os
import logging
from typing import Any, Dict

logger = logging.getLogger("utils.storage")

class JsonHandler:
    def __init__(self, filepath: str):
        self.filepath = filepath

    def load(self, default: Dict[str, Any] = None) -> Dict[str, Any]:
        if default is None:
            default = {}
        if not os.path.exists(self.filepath):
            return default
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load JSON ({self.filepath}): {e}")
            return default

    def save(self, data: Dict[str, Any]):
        try:
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save JSON ({self.filepath}): {e}")
