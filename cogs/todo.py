import discord
from discord import app_commands, ui
from discord.ext import commands
import logging
import os
import datetime
import uuid
from typing import Optional, Dict, Any
from utils.storage import JsonHandler

DATA_FILE = os.path.join("data", "todo_settings.json")

class ToDo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = JsonHandler(DATA_FILE)
        self.data = self.db.load()

    def save_data(self):
        self.db.save(self.data)

    def get_guild_config(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        if gid not in self.data: self.data[gid] = {}
        defaults = {"role_ids": [], "tasks": {}, "profiles": {}}
        for k, v in defaults.items():
            if k not in self.data[gid]: self.data[gid][k] = v
        return self.data[gid]

    def get_user_profile(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        g_conf = self.get_guild_config(guild_id)
        uid = str(user_id)
        if uid not in g_conf["profiles"]: g_conf["profiles"][uid] = {}
        p = g_conf["profiles"][uid]
        if "default_channel_id" not in p: p["default_channel_id"] = None
        if "mention_role_ids" not in p: p["mention_role_ids"] = None 
        return p

    def save_task(self, guild_id: int, message_id: int, title: str, description: str, author_id: int):
        conf = self.get_guild_config(guild_id)
        conf["tasks"][str(message_id)] = {
            "title": title, "description": description, "status": "open", "author_id": author_id, "created_at": datetime.datetime.now().isoformat()
        }
        self.save_data()

    def get_task(self, guild_id: int, message_id: int) -> Optional[Dict[str, Any]]:
        conf = self.get_guild_config(guild_id)
        return conf["tasks"].get(str(message_id))

    def update_task_status(self, guild_id: int, message_id: int, status: str):
        conf = self.get_guild_config(guild_id)
        mid = str(message_id)
        if mid in conf["tasks"]:
            conf["tasks"][mid]["status"] = status
            self.save_data()

    def delete_task_data(self, guild_id: int, message_id: int):
        conf = self.get_guild_config(guild_id)
        mid = str(message_id)
        if mid in conf["tasks"]:
            del conf["tasks"][mid]
            self.save_data()

    todo_group = app_commands.Group(name="todo", description="ToDo管理機能")
    setup_group = app_commands.Group(name="setup", description="ToDo機能の設定(管理者用)", parent=todo_group)
    my_group = app_commands.Group(name="my", description="ToDo機能の個人設定", parent=todo_group)

    @setup_group.command(name="add", description="デフォルトの通知対象ロールを追加")
    async def setup_add(self, itx: discord.Interaction, role: discord.Role):
        conf = self.get_guild_config(itx.guild_id)
        if role.id not in conf["role_ids"]:
            conf["role_ids"].append(role.id)
            self.save_data()
            await itx.response.send_message(f"✅ 追加しました: {role.mention}", ephemeral=True)
        else: await itx.response.send_message("既に追加されています。", ephemeral=True)

    @setup_group.command(name="remove", description="デフォルトの通知対象ロールを削除")
    async def setup_remove(self, itx: discord.Interaction, role: discord.Role):
        conf = self.get_guild_config(itx.guild_id)
        if role.id in conf["role_ids"]:
            conf["role_ids"].remove(role.id)
            self.save_data()
            await itx.response.send_message(f"🗑️ 削除しました: {role.mention}", ephemeral=True)
        else: await itx.response.send_message("設定されていません。", ephemeral=True)

    @setup_group.command(name="list", description="現在設定されているデフォルト通知先を表示")
    async def setup_list(self, itx: discord.Interaction):
        conf = self.get_guild_config(itx.guild_id)
        mentions = [itx.guild.get_role(rid).mention for rid in conf.get("role_ids", []) if itx.guild.get_role(rid)]
        await itx.response.send_message(f"📋 **サーバーデフォルト通知先:**\n" + "\n".join(mentions) if mentions else "なし", ephemeral=True)

    @my_group.command(name="setup", description="自分のタスクの投稿先や通知設定を変更")
    async def my_setup(self, itx: discord.Interaction, channel: Optional[discord.TextChannel] = None, add: Optional[discord.Role] = None, remove: Optional[discord.Role] = None, reset: Optional[bool] = False):
        p = self.get_user_profile(itx.guild_id, itx.user.id)
        msg = []
        if channel: p["default_channel_id"] = channel.id; msg.append(f"投稿先: {channel.mention}")
        if reset: p["mention_role_ids"] = None; msg.append("メンション: リセット")
        if add or remove:
            if p["mention_role_ids"] is None: p["mention_role_ids"] = []
            if add and add.id not in p["mention_role_ids"]: p["mention_role_ids"].append(add.id); msg.append(f"+ {add.mention}")
            if remove and remove.id in p["mention_role_ids"]: p["mention_role_ids"].remove(remove.id); msg.append(f"- {remove.mention}")
        self.save_data()
        await itx.response.send_message("\n".join(msg) or "変更なし", ephemeral=True)

    @my_group.command(name="status", description="自分の設定状況を確認")
    async def my_status(self, itx: discord.Interaction):
        p = self.get_user_profile(itx.guild_id, itx.user.id); g = self.get_guild_config(itx.guild_id)
        embed = discord.Embed(title=f"⚙️ ToDo個人設定: {itx.user.display_name}", color=discord.Color.blue())
        cid = p.get("default_channel_id"); ch = itx.guild.get_channel(cid) if cid else None
        embed.add_field(name="📮 投稿先", value=ch.mention if ch else "(未設定)", inline=True)
        
        u_mentions = p.get("mention_role_ids")
        if u_mentions is not None:
            m = [itx.guild.get_role(r).mention for r in u_mentions if itx.guild.get_role(r)]
            embed.add_field(name="📢 メンション (個人)", value=", ".join(m) or "なし", inline=False)
        else:
            m = [itx.guild.get_role(r).mention for r in g.get("role_ids", []) if itx.guild.get_role(r)]
            embed.add_field(name="📢 メンション (Default)", value=", ".join(m) or "なし", inline=False)
        await itx.response.send_message(embed=embed, ephemeral=True)

    @todo_group.command(name="new", description="新しいタスクを作成")
    async def new_todo(self, itx: discord.Interaction, ref_channel: Optional[discord.TextChannel] = None):
        await itx.response.send_modal(ToDoCreateModal(ref_channel))

class ToDoCreateModal(ui.Modal, title="新規タスク作成"):
    def __init__(self, ref_channel):
        super().__init__()
        self.ref_channel = ref_channel
        self.task_title = ui.TextInput(label="タスク件名", placeholder="未入力で自動ID", max_length=100)
        self.task_desc = ui.TextInput(label="詳細内容", style=discord.TextStyle.paragraph, required=False, max_length=2000)
        self.add_item(self.task_title); self.add_item(self.task_desc)

    async def on_submit(self, itx: discord.Interaction):
        cog = itx.client.get_cog("ToDo")
        conf = cog.get_guild_config(itx.guild_id)
        profile = cog.get_user_profile(itx.guild_id, itx.user.id)
        
        target_channel = itx.channel
        if profile.get("default_channel_id"):
            found = itx.guild.get_channel(profile["default_channel_id"])
            if found: target_channel = found

        t_rids = profile.get("mention_role_ids") if profile.get("mention_role_ids") is not None else conf.get("role_ids", [])
        mentions = " ".join([f"<@&{rid}>" for rid in t_rids])
        
        title = self.task_title.value.strip() or f"Task-{str(uuid.uuid4())[:8]}"
        embed = discord.Embed(title=f"📝 {title}", description=self.task_desc.value, color=discord.Color.orange(), timestamp=datetime.datetime.now())
        embed.set_author(name=itx.user.display_name, icon_url=itx.user.display_avatar.url)
        if self.ref_channel: embed.add_field(name="関連チャンネル", value=self.ref_channel.mention, inline=False)
        embed.set_footer(text="Status: Open")

        try:
            msg = await target_channel.send(content=mentions, embed=embed, view=ToDoView())
            cog.save_task(itx.guild_id, msg.id, title, self.task_desc.value, itx.user.id)
            await itx.response.send_message(f"✅ タスク作成完了: {msg.jump_url}", ephemeral=True)
        except Exception as e:
            await itx.response.send_message(f"❌ エラー: {e}", ephemeral=True)

class ToDoView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Text", style=discord.ButtonStyle.secondary, emoji="📄")
    async def show_text(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("ToDo")
        task = cog.get_task(itx.guild_id, itx.message.id)
        content = task.get("description") if task else (itx.message.embeds[0].description if itx.message.embeds else "No Content")
        await itx.response.send_message(content or "No Content", ephemeral=True)

    @discord.ui.button(label="Resolve", style=discord.ButtonStyle.success)
    async def complete(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("ToDo"); task = cog.get_task(itx.guild_id, itx.message.id)
        if task and task.get("status") == "completed": await itx.response.send_message("既に完了済みです", ephemeral=True); return
        cog.update_task_status(itx.guild_id, itx.message.id, "completed")
        
        embed = itx.message.embeds[0]; embed.color = discord.Color.green()
        embed.title = f"✅ Resolved: {embed.title.replace('📝 ', '')}"
        embed.set_footer(text=f"Resolved by: {itx.user.display_name}")
        self.remove_item(button); await itx.message.edit(embed=embed, view=self)
        await itx.response.send_message("👍 Resolved!", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("ToDo"); cog.delete_task_data(itx.guild_id, itx.message.id)
        await itx.message.delete(); await itx.response.send_message("🗑️ Deleted", ephemeral=True)

async def setup(bot: commands.Bot):
    bot.add_view(ToDoView())
    await bot.add_cog(ToDo(bot))
