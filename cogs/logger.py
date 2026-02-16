import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import logging
import time
from typing import Optional, Dict, Any, List

# Logger setup
logger = logging.getLogger("discord_bot.cogs.logger")
DATA_FILE = os.path.join("data", "log_settings.json")

class Logger(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.settings = self.load_settings()
        self.channel_cooldowns: Dict[int, float] = {}

    def load_settings(self) -> Dict[str, Any]:
        if not os.path.exists(DATA_FILE): return {}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load log settings: {e}")
            return {}

    def save_settings(self):
        try:
            os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
            with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(self.settings, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save log settings: {e}")

    def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        if gid not in self.settings: self.settings[gid] = {}
        defaults = {
            "reception_role_ids": [],
            "ignore": {"roles": [], "categories": [], "channels": []},
            "routes": {"channels": {}, "categories": {}},
            "cooldown_seconds": 0
        }
        guild_settings = self.settings[gid]
        for key, value in defaults.items():
            if key not in guild_settings: guild_settings[key] = value
        
        # Migration logic (Old setup -> New list)
        if "reception_role_id" in guild_settings:
            if guild_settings["reception_role_id"]:
                guild_settings["reception_role_ids"] = [guild_settings["reception_role_id"]]
            del guild_settings["reception_role_id"]
            self.save_settings()

        # Cleanup
        for key in ["log_channel_id", "watch", "mode", "whitelist"]:
            if key in guild_settings: del guild_settings[key]; self.save_settings()
        return guild_settings

    # --- Logic ---

    def get_route_channel(self, source_channel: discord.TextChannel) -> Optional[discord.TextChannel]:
        if not source_channel.guild: return None
        settings = self.get_guild_settings(source_channel.guild.id)
        routes = settings.get("routes", {})
        
        src_id = str(source_channel.id)
        guild = source_channel.guild

        if src_id in routes.get("channels", {}):
            return guild.get_channel(int(routes["channels"][src_id]))

        if source_channel.category_id:
            cat_id = str(source_channel.category_id)
            if cat_id in routes.get("categories", {}):
                return guild.get_channel(int(routes["categories"][cat_id]))
        return None

    def _is_ignored(self, message: discord.Message, settings: dict) -> bool:
        ignore = settings.get("ignore", {})
        cid = message.channel.id
        cat_id = message.channel.category.id if message.channel.category else None

        ignored_role_ids = set(ignore.get("roles", []))
        if ignored_role_ids:
            member_role_ids = {role.id for role in message.author.roles}
            if not member_role_ids.isdisjoint(ignored_role_ids): return True

        if cid in ignore.get("channels", []): return True
        if cat_id and cat_id in ignore.get("categories", []): return True

        return False

    # --- Listener ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot: return

        settings = self.get_guild_settings(message.guild.id)
        
        if self._is_ignored(message, settings): return

        dest_channel = self.get_route_channel(message.channel)
        if not dest_channel: return

        # Cooldown Check
        cd_sec = settings.get("cooldown_seconds", 0)
        if cd_sec > 0:
            last_time = self.channel_cooldowns.get(message.channel.id, 0)
            now = time.time()
            if now - last_time < cd_sec: return

        # Build Embed
        embed = discord.Embed(description=message.content or "[(内容なし)]", color=discord.Color.light_grey(), timestamp=message.created_at)
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="チャンネル", value=message.channel.mention, inline=True)
        embed.add_field(name="リンク", value=f"[ジャンプ]({message.jump_url})", inline=True)

        if message.attachments:
            embed.add_field(name="添付ファイル", value=", ".join([a.filename for a in message.attachments]), inline=False)
        if message.reference and message.reference.cached_message:
            ref = message.reference.cached_message
            embed.add_field(name="返信先", value=f"{ref.author.display_name}: {ref.content[:50]}...", inline=False)

        role_ids = settings.get("reception_role_ids", [])
        content = " ".join([f"<@&{rid}>" for rid in role_ids]) if role_ids else None

        try:
            await dest_channel.send(content=content, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))
            if cd_sec > 0: self.channel_cooldowns[message.channel.id] = time.time()
        except Exception as e:
            logger.error(f"Failed to send log: {e}")

    # ====================================================
    # Commands Structure
    # ====================================================
    
    # Root Group
    log_group = app_commands.Group(name="logger", description="ログ設定")

    # 1. Route Group (監視・転送設定)
    route_group = app_commands.Group(name="route", description="監視対象と出力先の設定", parent=log_group)

    @route_group.command(name="add", description="監視対象を追加")
    async def route_add(self, itx: discord.Interaction, destination: discord.TextChannel, source_channel: discord.TextChannel = None, category: discord.CategoryChannel = None):
        if not source_channel and not category:
            await itx.response.send_message("エラー: 監視元を指定してください。", ephemeral=True); return
        settings = self.get_guild_settings(itx.guild_id); routes = settings["routes"]; msg = []
        if source_channel:
            routes["channels"][str(source_channel.id)] = destination.id
            msg.append(f"監視追加: {source_channel.mention} -> {destination.mention}")
        if category:
            routes["categories"][str(category.id)] = destination.id
            msg.append(f"監視追加: カテゴリ[{category.name}] -> {destination.mention}")
        self.save_settings()
        await itx.response.send_message("\n".join(msg), ephemeral=True)

    @route_group.command(name="remove", description="監視設定を削除")
    async def route_remove(self, itx: discord.Interaction, source_channel: discord.TextChannel = None, category: discord.CategoryChannel = None):
        settings = self.get_guild_settings(itx.guild_id); routes = settings["routes"]; msg = []
        if source_channel and str(source_channel.id) in routes["channels"]:
            del routes["channels"][str(source_channel.id)]; msg.append(f"監視削除: {source_channel.mention}")
        if category and str(category.id) in routes["categories"]:
            del routes["categories"][str(category.id)]; msg.append(f"監視削除: カテゴリ[{category.name}]")
        self.save_settings()
        await itx.response.send_message("\n".join(msg) or "設定が見つかりませんでした。", ephemeral=True)

    # 2. Ignore Group (除外設定)
    ignore_group = app_commands.Group(name="ignore", description="ログ監視から除外する設定", parent=log_group)

    @ignore_group.command(name="add", description="指定した対象を無視")
    async def ignore_add(self, itx: discord.Interaction, role: discord.Role = None, category: discord.CategoryChannel = None, channel: discord.TextChannel = None):
        settings = self.get_guild_settings(itx.guild_id); ignore = settings["ignore"]; msg = []
        if role and role.id not in ignore["roles"]: ignore["roles"].append(role.id); msg.append(f"ロール無視: {role.mention}")
        if category and category.id not in ignore["categories"]: ignore["categories"].append(category.id); msg.append(f"カテゴリ無視: {category.name}")
        if channel and channel.id not in ignore["channels"]: ignore["channels"].append(channel.id); msg.append(f"チャンネル無視: {channel.mention}")
        self.save_settings()
        await itx.response.send_message("\n".join(msg) or "既に追加されています。", ephemeral=True)

    @ignore_group.command(name="remove", description="無視設定を解除")
    async def ignore_remove(self, itx: discord.Interaction, role: discord.Role = None, category: discord.CategoryChannel = None, channel: discord.TextChannel = None):
        settings = self.get_guild_settings(itx.guild_id); ignore = settings["ignore"]; msg = []
        if role and role.id in ignore["roles"]: ignore["roles"].remove(role.id); msg.append(f"解除: {role.mention}")
        if category and category.id in ignore["categories"]: ignore["categories"].remove(category.id); msg.append(f"解除: {category.name}")
        if channel and channel.id in ignore["channels"]: ignore["channels"].remove(channel.id); msg.append(f"解除: {channel.mention}")
        self.save_settings()
        await itx.response.send_message("\n".join(msg) or "設定が見つかりませんでした。", ephemeral=True)

    # 3. Notify Group (旧 Setup - 通知先設定)
    notify_group = app_commands.Group(name="notify", description="ログ発生時のメンション先設定", parent=log_group)

    @notify_group.command(name="add", description="メンションロールを追加")
    async def notify_add(self, itx: discord.Interaction, role: discord.Role):
        settings = self.get_guild_settings(itx.guild_id)
        current = settings.get("reception_role_ids", [])
        if role.id not in current:
            current.append(role.id); settings["reception_role_ids"] = current
            self.save_settings()
            await itx.response.send_message(f"✅ 通知先に {role.mention} を追加しました。", ephemeral=True)
        else: await itx.response.send_message(f"⚠️ {role.mention} は既に追加されています。", ephemeral=True)

    @notify_group.command(name="remove", description="メンションロールを削除")
    async def notify_remove(self, itx: discord.Interaction, role: discord.Role):
        settings = self.get_guild_settings(itx.guild_id)
        current = settings.get("reception_role_ids", [])
        if role.id in current:
            current.remove(role.id); settings["reception_role_ids"] = current
            self.save_settings()
            await itx.response.send_message(f"🗑️ 通知先から {role.mention} を削除しました。", ephemeral=True)
        else: await itx.response.send_message(f"⚠️ {role.mention} は設定されていません。", ephemeral=True)

    @notify_group.command(name="list", description="通知先ロール一覧")
    async def notify_list(self, itx: discord.Interaction):
        settings = self.get_guild_settings(itx.guild_id)
        current = settings.get("reception_role_ids", [])
        if not current: await itx.response.send_message("通知先ロールは設定されていません。", ephemeral=True); return
        mentions = []
        for rid in current:
            role = itx.guild.get_role(rid); mentions.append(role.mention if role else f"(削除済: {rid})")
        await itx.response.send_message(f"📢 **通知先ロール一覧:**\n" + "\n".join(mentions), ephemeral=True)

    # 4. Config Group (システム設定 & ステータス)
    config_group = app_commands.Group(name="config", description="システム設定・状況確認", parent=log_group)

    @config_group.command(name="cooldown", description="連続通知の待機時間を設定 (0で無効)")
    @app_commands.describe(seconds="待機秒数 (例: 300)")
    async def config_cooldown(self, itx: discord.Interaction, seconds: int):
        if seconds < 0: await itx.response.send_message("⚠️ 秒数は0以上にしてください。", ephemeral=True); return
        settings = self.get_guild_settings(itx.guild_id)
        settings["cooldown_seconds"] = seconds; self.save_settings()
        msg = "✅ クールダウンを無効化しました。" if seconds == 0 else f"✅ クールダウンを **{seconds}秒** に設定しました。"
        await itx.response.send_message(msg, ephemeral=True)

    @config_group.command(name="status", description="現在の設定状況をすべて表示")
    async def config_status(self, itx: discord.Interaction):
        settings = self.get_guild_settings(itx.guild_id)
        ignore = settings["ignore"]; routes = settings["routes"]
        embed = discord.Embed(title="📋 ログ設定状況", color=discord.Color.blue())
        
        # System
        cd_sec = settings.get("cooldown_seconds", 0)
        embed.add_field(name="⚙️ Config", value=f"Cooldown: **{cd_sec}秒**", inline=False)
        
        # Notify
        setup_list = []
        for rid in settings.get("reception_role_ids", []):
            r = itx.guild.get_role(rid); setup_list.append(r.mention if r else str(rid))
        embed.add_field(name="📢 Notify (通知先)", value=", ".join(setup_list) or "なし", inline=False)

        # Route
        r_list = []
        for src, dest in routes["categories"].items():
            s = itx.guild.get_channel(int(src)); d = itx.guild.get_channel(int(dest))
            r_list.append(f"📂 {s.name if s else src} -> {d.mention if d else dest}")
        for src, dest in routes["channels"].items():
            s = itx.guild.get_channel(int(src)); d = itx.guild.get_channel(int(dest))
            r_list.append(f"#️⃣ {s.mention if s else src} -> {d.mention if d else dest}")
        embed.add_field(name="👁️ Route (監視)", value="\n".join(r_list) or "なし", inline=False)

        # Ignore
        i_list = []
        for rid in ignore["roles"]: r = itx.guild.get_role(rid); i_list.append(f"👤 {r.mention if r else rid}")
        for cid in ignore["categories"]: c = itx.guild.get_channel(cid); i_list.append(f"📂 {c.name if c else cid}")
        for cid in ignore["channels"]: c = itx.guild.get_channel(cid); i_list.append(f"#️⃣ {c.mention if c else cid}")
        embed.add_field(name="🚫 Ignore (無視)", value="\n".join(i_list) or "なし", inline=False)

        await itx.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Logger(bot))
