import discord
import logging

logger = logging.getLogger("utils.persistent_views")

_persistent_view_classes = []

def persistent_view(cls):
    """
    Viewクラスに付与するデコレータ。
    """
    _persistent_view_classes.append(cls)
    return cls

def register_all(bot):
    """
    Botの setup_hook で呼び出し、一括登録します。
    """
    for view_cls in _persistent_view_classes:
        try:
            bot.add_view(view_cls())
            logger.info(f"Registered persistent view: {view_cls.__name__}")
        except Exception as e:
            logger.error(f"Failed to register persistent view {view_cls.__name__}: {e}")
