# utils/persistent_views.py
from __future__ import annotations
from typing import List, Type
import logging
import discord

log = logging.getLogger("discordbot")

_REGISTERED: List[Type[discord.ui.View]] = []

def persistent_view(cls: Type[discord.ui.View]) -> Type[discord.ui.View]:
    """
    View クラスを登録するだけの軽量デコレータ。
    - __init__ は書き換えない（各クラスで timeout=None を指定してください）
    - 起動時に register_all() で bot.add_view される
    """
    if cls not in _REGISTERED:
        _REGISTERED.append(cls)
    return cls

def register_all(bot: discord.Client) -> None:
    """
    import 済みの persistent View を一括登録。
    load_extension が終わった後に呼んでください。
    """
    for cls in _REGISTERED:
        try:
            bot.add_view(cls())  # 各 View は custom_id を固定している前提
            log.info("Registered persistent view: %s", cls.__name__)
        except Exception:
            log.exception("[persistent] failed to add %s", cls.__name__)

