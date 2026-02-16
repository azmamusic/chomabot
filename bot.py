import os
import sys
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import discord
from discord.ext import commands
from dotenv import load_dotenv
from typing import Literal, Optional

# --- Logging Setup ---
# Standardize logging format, no emojis in logs
LOG_FILE = "bot.log"
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=3,
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[file_handler, logging.StreamHandler()],
)
logger = logging.getLogger("discord_bot")

# --- Environment Setup ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# --- Bot Setup ---
# Enable necessary intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.reactions = True

class MyBot(commands.Bot):
    def __init__(self):
        # prefix is required for text commands like !sync
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        # Load extensions
        initial_extensions = [
            "cogs.logger",   # Core logging functionality
            "cogs.roles",    # Merged Qualify and Roles
            "cogs.tickets",  # Ticket system
            "cogs.todo",     # Todo list
            "cogs.move",     # Message mover
            "cogs.members",  # Manage members
        ]
        
        for ext in initial_extensions:
            try:
                await self.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                logger.error(f"Failed to load extension {ext}: {e}")

        # --- Auto Sync for Dev/Test Guilds ---
        # Get comma-separated Guild IDs from .env
        dev_guild_ids_str = os.getenv("DEV_GUILD_IDS")
        
        if dev_guild_ids_str:
            # Parse CSV string to list of integers
            guild_ids = [
                int(x.strip()) for x in dev_guild_ids_str.split(",") 
                if x.strip().isdigit()
            ]

            for g_id in guild_ids:
                try:
                    guild = discord.Object(id=g_id)
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    logger.info(f"✅ Auto-synced commands to guild: {g_id}")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to auto-sync to guild {g_id}: {e}")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")

# --- Entry Point ---
if __name__ == "__main__":
    if not TOKEN:
        logger.critical("DISCORD_TOKEN is not set in .env")
        sys.exit(1)

    bot = MyBot()

    # --- Utility Command: Sync ---
    # Can be used by Administrators (not just the owner)
    @bot.command()
    @commands.has_permissions(administrator=True)
    async def sync(ctx, spec: Optional[Literal["global", "clear", "purge_global"]] = None):
        """
        コマンド同期用ユーティリティ
        !sync               -> 現在のサーバーにコマンドを即時反映 (開発用)
        !sync global        -> 全サーバーに反映 (本番用)
        !sync clear         -> 現在のサーバーのコマンドを全消去
        !sync purge_global  -> グローバルコマンドを「全消去」 (重複解消用)
        """
        if spec == "global":
            synced = await ctx.bot.tree.sync()
            await ctx.send(f"🌍 Synced {len(synced)} commands globally. (Propagation may take time)")
            logger.info(f"Synced {len(synced)} commands globally.")
        
        elif spec == "clear":
            ctx.bot.tree.clear_commands(guild=ctx.guild)
            await ctx.bot.tree.sync(guild=ctx.guild)
            await ctx.send("🧹 Cleared all commands in this guild.")
            logger.info(f"Cleared commands in guild {ctx.guild.id}.")

        elif spec == "purge_global":
            # ★ ここが重要！グローバルコマンドを空にして同期する
            msg = await ctx.send("🗑️ グローバルコマンドの全削除を開始します...")
            
            # 1. 内部のグローバルコマンド定義を空にする
            ctx.bot.tree.clear_commands(guild=None)
            
            # 2. 空の状態をDiscordに同期（＝削除）
            await ctx.bot.tree.sync()
            
            await msg.edit(content="✅ **グローバルコマンドを全削除しました。**\nスマホの重複表示が消えるまで、最大1時間ほどかかります。\n\n⚠️ **重要:** コマンド定義を再ロードするために、**必ずBotを再起動してください**。")
            logger.info("Purged global commands.")

        else:
            # Default: Sync to current guild
            ctx.bot.tree.copy_global_to(guild=ctx.guild)
            synced = await ctx.bot.tree.sync(guild=ctx.guild)
            await ctx.send(f"🔄 Synced {len(synced)} commands to this guild.")
            logger.info(f"Synced {len(synced)} commands to guild {ctx.guild.id}.")

    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.critical(f"Bot execution failed: {e}")
