import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
import os
import logging
import datetime
import asyncio
import re
from typing import Dict, Any, Optional, List, Union
from utils.storage import JsonHandler
from utils.persistent_views import persistent_view

logger = logging.getLogger("discord_bot.cogs.tickets")

DATA_FILE = os.path.join("data", "tickets_profiles.json")
TIMER_DATA_FILE = os.path.join("data", "tickets_timer.json")

DEFAULT_TIMEOUT_HOURS = 48
DEFAULT_AUTO_CLOSE_DAYS = 60
DEFAULT_MAX_SLOTS = 3
DEFAULT_REUSE_CHANNEL = False
DEFAULT_NOTIFY_ENABLED = True
DEFAULT_AUTO_CLOSE_ENABLED = True
DEFAULT_LOG_COOLDOWN = 300

# ====================================================
# Data Management
# ====================================================
class TicketDataManager:
    def __init__(self):
        self.profiles_handler = JsonHandler(DATA_FILE)
        self.timers_handler = JsonHandler(TIMER_DATA_FILE)
        
        self.profiles = self.profiles_handler.load()
        self.timers = self.timers_handler.load()
        
        self._profiles_dirty = False
        self._timers_dirty = False

    def save_profiles(self):
        self._profiles_dirty = True

    def save_timers(self):
        self._timers_dirty = True
        
    def flush(self):
        if self._profiles_dirty:
            self.profiles_handler.save(self.profiles)
            self._profiles_dirty = False
        if self._timers_dirty:
            self.timers_handler.save(self.timers)
            self._timers_dirty = False

    def get_guild_config(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        if gid not in self.profiles:
            self.profiles[gid] = {}
        if gid not in self.timers:
            self.timers[gid] = {}
        
        g = self.profiles[gid]
        defaults = {
            "assignee_role_id": None, "assignee_qual_role_id": None,
            "profiles": {}, "attributes": {}, "category_id": None, "name_format": None,
            "mention_roles": [], "log_roles": [], "ignore_roles": [], "template": None, "transcript_id": None,
            "cooldown": DEFAULT_LOG_COOLDOWN, "reuse_channel": DEFAULT_REUSE_CHANNEL,
            "max_slots": DEFAULT_MAX_SLOTS, "notify_enabled": DEFAULT_NOTIFY_ENABLED,
            "timeout_hours": DEFAULT_TIMEOUT_HOURS, "auto_close_days": DEFAULT_AUTO_CLOSE_DAYS,
            "auto_close_enabled": DEFAULT_AUTO_CLOSE_ENABLED
        }
        for k, v in defaults.items():
            if k not in g:
                g[k] = v
        return g

    def get_user_profile(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        g = self.get_guild_config(guild_id)
        uid = str(user_id)
        if uid not in g["profiles"]:
            g["profiles"][uid] = {}
        p = g["profiles"][uid]
        defaults = {
            "category_id": None, "template": None, "name_format": None,
            "mention_roles": None, "log_roles": None, "ignore_roles": None, "blacklist": [], "attributes": {},
            "reuse_channel": None, "max_slots": None, "notify_enabled": None,
            "timeout_hours": None, "auto_close_enabled": None, "auto_close_days": None,
            "transcript_id": None, "cooldown": None
        }
        for k, v in defaults.items():
            if k not in p:
                p[k] = v
        return p

# ====================================================
# UI Classes
# ====================================================
@persistent_view
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="依頼を作成する", style=discord.ButtonStyle.success, emoji="📝", custom_id="panel_create_btn")
    async def create_btn(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        if not cog:
            return
        g_conf = cog.db.get_guild_config(itx.guild_id)
        attributes = g_conf.get("attributes", {})
        if attributes:
            await itx.response.send_message("🔍 担当者の選定基準を選択してください。", view=AttributeSelectView(list(attributes.keys())), ephemeral=True)
        else:
            options = cog.get_assignee_options(itx.guild, sort_key=None)
            if not options:
                await itx.response.send_message("⚠️ 受付可能な担当者が見つかりません。", ephemeral=True)
                return
            await itx.response.send_message("担当者を選択してください。", view=AssigneeSelectView(options), ephemeral=True)

class AttributeSelectView(discord.ui.View):
    def __init__(self, attributes: List[str]):
        super().__init__(timeout=60)
        options = [discord.SelectOption(label="指定なし (標準)", value="NONE")]
        for attr in attributes:
            options.append(discord.SelectOption(label=attr, value=attr))
        self.select = discord.ui.Select(placeholder="並び替え基準...", options=options, min_values=1, max_values=1)
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, itx: discord.Interaction):
        cog = itx.client.get_cog("Tickets")
        sort_key = None if self.select.values[0] == "NONE" else self.select.values[0]
        options = cog.get_assignee_options(itx.guild, sort_key=sort_key)
        if not options:
            await itx.response.send_message("⚠️ 候補者がいません。", ephemeral=True)
            return
        await itx.response.edit_message(content="担当者を選択してください。", view=AssigneeSelectView(options))

class AssigneeSelectView(discord.ui.View):
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(timeout=180)
        self.select = discord.ui.Select(placeholder="担当者を選択...", options=options[:25], min_values=1, max_values=1)
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, itx: discord.Interaction):
        target = itx.guild.get_member(int(self.select.values[0]))
        if not target:
            await itx.response.send_message("エラー: ユーザー不明", ephemeral=True)
            return
        cog = itx.client.get_cog("Tickets")
        err = cog.check_accept_status(itx.guild, target, itx.user)
        if err:
            await itx.response.send_message(err, ephemeral=True)
        else:
            await itx.response.send_modal(ContractModal(target))

class ContractModal(discord.ui.Modal, title="依頼内容 (1/2: 契約情報)"):
    def __init__(self, assignee: discord.Member):
        super().__init__()
        self.assignee = assignee
        self.t_name = discord.ui.TextInput(label="依頼者名義", max_length=50)
        self.t_title = discord.ui.TextInput(label="楽曲タイトル", max_length=100)
        self.t_type = discord.ui.TextInput(label="依頼形態", placeholder="ボーカルエディット、ミックス、マスタリング、etc", max_length=50)
        self.t_deadline = discord.ui.TextInput(label="希望納期", max_length=50)
        self.t_budget = discord.ui.TextInput(label="予算", required=False, placeholder="「未定」「見積もり」等可", max_length=50)
        for i in [self.t_name, self.t_title, self.t_type, self.t_deadline, self.t_budget]:
            self.add_item(i)

    async def on_submit(self, itx: discord.Interaction):
        cog = itx.client.get_cog("Tickets")
        try:
            await itx.response.defer(ephemeral=True)
        except discord.NotFound:
            logger.warning(f"Interaction timed out during ContractModal submit for {itx.user.display_name}")
            return
        
        try:
            ch, msg = await cog.create_ticket_entry(itx.guild, itx.user, self.assignee, self.t_name.value, self.t_title.value, self.t_type.value, self.t_deadline.value, self.t_budget.value)
            await itx.followup.send(f"✅ チケットを作成しました: {msg.jump_url}", ephemeral=True)
        except Exception as e:
            await itx.followup.send(f"エラー: {e}", ephemeral=True)

class TechModal(discord.ui.Modal, title="依頼内容 (2/2: 技術情報)"):
    def __init__(self):
        super().__init__()
        self.t_data = discord.ui.TextInput(label="データURL", max_length=200)
        self.t_ref = discord.ui.TextInput(label="リファレンスURL", required=False, max_length=200)
        self.t_bpm = discord.ui.TextInput(label="BPM", max_length=50)
        self.t_key = discord.ui.TextInput(label="Key", max_length=50)
        self.t_rem = discord.ui.TextInput(label="備考", style=discord.TextStyle.paragraph, required=False, max_length=1000)
        for i in [self.t_data, self.t_ref, self.t_bpm, self.t_key, self.t_rem]:
            self.add_item(i)

    async def on_submit(self, itx: discord.Interaction):
        if not itx.message.embeds:
            return
        embed = itx.message.embeds[0]
        embed.color = discord.Color.green()
        data_map = [("📂 データ", self.t_data.value), ("🎧 リファレンス", self.t_ref.value), ("BPM", self.t_bpm.value), ("Key", self.t_key.value), ("📝 備考", self.t_rem.value)]
        new_fields = [f for f in embed.fields if "次のステップ" not in f.name]
        embed.clear_fields()
        for f in new_fields:
            embed.add_field(name=f.name, value=f.value, inline=f.inline)
        embed.add_field(name="──────────────", value="**🎵 技術詳細**", inline=False)
        for name, val in data_map:
            if val:
                embed.add_field(name=name, value=val, inline=(name!="📂 データ" and name!="📝 備考"))
        await itx.message.edit(embed=embed, view=TicketControlView())
        cog = itx.client.get_cog("Tickets")
        if cog:
            await cog.log_to_forum(itx.channel, embed=embed, is_update=True, target_msg_id=itx.message.id)
        await itx.response.send_message("✅ 詳細を保存しました！", ephemeral=False)

@persistent_view
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎵 詳細入力", style=discord.ButtonStyle.primary, custom_id="btn_tech")
    async def btn_tech(self, itx: discord.Interaction, button: discord.ui.Button): 
        try:
            await itx.response.send_modal(TechModal())
        except discord.NotFound:
            logger.warning(f"Interaction timed out during btn_tech for {itx.user.display_name}")

    @discord.ui.button(label="⚙️ 管理", style=discord.ButtonStyle.secondary, custom_id="btn_manage", row=1)
    async def btn_manage(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        t_data = cog.db.timers.get(str(itx.guild_id), {}).get(str(itx.channel.id), {})
        is_assignee = t_data.get("assignee_id") == itx.user.id
        is_admin = itx.user.guild_permissions.manage_roles
        if not (is_assignee or is_admin):
            await itx.response.send_message("担当者のみ使用可能です。", ephemeral=True)
            return
        embed = await cog.create_ticket_dashboard_embed(itx.channel, t_data)
        await itx.response.send_message(embed=embed, view=AssigneeMenuView(itx.channel, itx.message.id), ephemeral=True)
 
class AssigneeMenuView(discord.ui.View):
    def __init__(self, target_channel, ticket_msg_id):
        super().__init__(timeout=180)
        self.target_channel = target_channel
        self.ticket_msg_id = ticket_msg_id

    @discord.ui.button(label="⏱️ タイマー設定", style=discord.ButtonStyle.secondary)
    async def timer_settings(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        t = cog.db.timers.get(str(itx.guild_id), {}).get(str(self.target_channel.id), {})
        await itx.response.send_modal(TimerEditModal(t.get("timeout_hours", DEFAULT_TIMEOUT_HOURS), t.get("auto_close_days", DEFAULT_AUTO_CLOSE_DAYS), self.target_channel))

    @discord.ui.button(label="📂 提出先設定", style=discord.ButtonStyle.success)
    async def set_url(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.send_modal(SubmitUrlModalExt(self.target_channel))

    @discord.ui.button(label="📋 タスクリスト編集", style=discord.ButtonStyle.primary, row=1)
    async def edit_tasks(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        gid, cid = str(itx.guild_id), str(self.target_channel.id)
        t_data = cog.db.timers.get(gid, {}).get(cid, {})
        current_tasks = t_data.get("tasks", {}).get(str(self.ticket_msg_id), [])
        text_val = "\n".join([t["name"] for t in current_tasks])
        await itx.response.send_modal(TaskListEditModal(self.target_channel, self.ticket_msg_id, text_val, is_from_forum_panel=False))

    @discord.ui.button(label="✅ 完了/クローズ", style=discord.ButtonStyle.danger, row=1)
    async def close(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.send_message("処理を選択:", view=AssigneeCloseView(self.target_channel, self.ticket_msg_id), ephemeral=True)

class TaskListEditModal(discord.ui.Modal, title="タスクリスト編集"):
    def __init__(self, target_channel, ticket_msg_id, current_text="", is_from_forum_panel=False):
        super().__init__()
        self.target_channel = target_channel
        self.ticket_msg_id = ticket_msg_id
        self.is_from_forum_panel = is_from_forum_panel
        default_tasks="受領確認・請求書提出\nボーカルエディット\nミックス\nマスタリング\n音源提出\nリテイク対応\nMUX"
        self.input_text = discord.ui.TextInput(
            label="タスク (改行またはカンマ区切り)",
            style=discord.TextStyle.paragraph,
            default=current_text or default_tasks,
            required=False,
        )
        self.add_item(self.input_text)

    async def on_submit(self, itx: discord.Interaction):
        cog = itx.client.get_cog("Tickets")
        gid, cid = str(itx.guild_id), str(self.target_channel.id)
        
        raw_text = self.input_text.value
        # Split by newline or comma
        parts = re.split(r'[,\n]+', raw_text)
        new_names = [p.strip() for p in parts if p.strip()]

        if cid in cog.db.timers.get(gid, {}):
            if "tasks" not in cog.db.timers[gid][cid]:
                cog.db.timers[gid][cid]["tasks"] = {}
            old_list = cog.db.timers[gid][cid]["tasks"].get(str(self.ticket_msg_id), [])
            # Map old task completion status: name -> completed
            status_map = {t["name"]: t["completed"] for t in old_list}
            
            new_list = []
            for name in new_names:
                new_list.append({
                    "name": name,
                    "completed": status_map.get(name, False)
                })
            
            cog.db.timers[gid][cid]["tasks"][str(self.ticket_msg_id)] = new_list
            cog.db.save_timers()

            await cog.log_to_forum(self.target_channel, content="📝 **タスクリストが更新されました**", target_msg_id=self.ticket_msg_id)
            
            if self.is_from_forum_panel:
                embed = discord.Embed(title="📋 タスク操作パネル", color=discord.Color.blue())
                desc = f"**【操作ログ: ✅ 『{target_name}』を完了しました】**\n\n"
                for t in new_list:
                    mark = "✅" if t["completed"] else "☑️"
                    desc += f"{mark} {t['name']}\n"
                embed.description = desc or "タスクなし"
                await itx.response.edit_message(embed=embed, view=TaskActionView(self.target_channel, self.ticket_msg_id, new_list))
            else:
                await itx.response.send_message(f"✅ タスクリストを更新しました ({len(new_list)}件)", ephemeral=True)
        else:
            await itx.response.send_message("⚠️ チケットデータが見つかりません。", ephemeral=True)

@persistent_view
class ForumTaskLogView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 タスク操作", style=discord.ButtonStyle.secondary, custom_id="forum_task_op")
    async def open_panel(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        # Find ticket ID from forum thread ID (itx.channel_id)
        gid = str(itx.guild_id)
        cid = None
        target_data = None
        target_channel = None

        # 検索に時間がかかる可能性があるため、先にdeferする
        await itx.response.defer(ephemeral=True)
        
        # Search timers for matching mirror_thread_id
        if gid in cog.db.timers:
            for ch_id, data in list(cog.db.timers[gid].items()):
                if data.get("mirror_thread_id") == itx.channel.id:
                    ch = itx.guild.get_channel(int(ch_id))
                    if not ch:
                        try:
                            ch = await itx.guild.fetch_channel(int(ch_id))
                        except discord.NotFound:
                            continue # 削除されたゴーストデータならスキップして次を探す
                            
                    if ch:
                        target_channel = ch
                        target_data = data
                        cid = ch_id
                        break

        if not target_channel:
            await itx.followup.send("⚠️ このスレッドに関連付けられたチケット（チャンネル）が見つかりません。", ephemeral=True)
            return

        active_tickets = target_data.get("active_tickets", [])
        if not active_tickets:
             await itx.followup.send("⚠️ 稼働中のチケットがありません。", ephemeral=True)
             return
             
        ticket_msg_id = active_tickets[-1]
        task_list = target_data.get("tasks", {}).get(str(ticket_msg_id), [])
        
        if not task_list:
             await itx.followup.send("✅ 全てのタスクが完了しているか、タスクがありません。", ephemeral=True)
             return

        embed = discord.Embed(title="📋 タスク操作パネル", color=discord.Color.blue())
        desc = ""
        for t in task_list:
            mark = "✅" if t["completed"] else "☑️"
            desc += f"{mark} {t['name']}\n"
        embed.description = desc or "タスクなし"

        await itx.followup.send(embed=embed, view=TaskActionView(target_channel, ticket_msg_id, task_list), ephemeral=True)

class TaskActionView(discord.ui.View):
    def __init__(self, target_channel, ticket_msg_id, task_list):
        super().__init__(timeout=180)
        self.target_channel = target_channel
        self.ticket_msg_id = ticket_msg_id
        self.task_list = task_list
        
        # Check for uncompleted tasks
        self.next_task = next((t for t in task_list if not t["completed"]), None)
        
        btn_label = f"▶️ タスクを進行する" if self.next_task else "🎉 全て完了"
        btn_style = discord.ButtonStyle.success if self.next_task else discord.ButtonStyle.secondary
        btn_disabled = self.next_task is None
        
        self.complete_next_btn = discord.ui.Button(label=btn_label, style=btn_style, disabled=btn_disabled)
        self.complete_next_btn.callback = self.complete_next
        self.add_item(self.complete_next_btn)
        
        self.edit_btn = discord.ui.Button(label="📝 リスト編集", style=discord.ButtonStyle.secondary)
        self.edit_btn.callback = self.edit_list
        self.add_item(self.edit_btn)

    async def complete_next(self, itx: discord.Interaction):
        if not self.next_task:
            return
        
        cog = itx.client.get_cog("Tickets")
        gid, cid = str(itx.guild_id), str(self.target_channel.id)
        
        if cid in cog.db.timers.get(gid, {}):
            tasks = cog.db.timers[gid][cid].get("tasks", {}).get(str(self.ticket_msg_id), [])
            target_name = self.next_task["name"]
            
            # Find and update
            for t in tasks:
                if t["name"] == target_name and not t["completed"]:
                    t["completed"] = True
                    break
            
            cog.db.timers[gid][cid]["tasks"][str(self.ticket_msg_id)] = tasks
            cog.db.save_timers()
            
            embed = discord.Embed(title="📋 タスク操作パネル", color=discord.Color.blue())
            desc = ""
            for t in tasks:
                mark = "✅" if t["completed"] else "☑️"
                desc += f"{mark} {t['name']}\n"
            embed.description = desc or "タスクなし"
            
            await itx.response.edit_message(embed=embed, view=TaskActionView(self.target_channel, self.ticket_msg_id, tasks))
        else:
            await itx.response.send_message("⚠️ エラー: データ不整合", ephemeral=True)

    async def edit_list(self, itx: discord.Interaction):
        current_text = "\n".join([t["name"] for t in self.task_list])
        await itx.response.send_modal(TaskListEditModal(self.target_channel, self.ticket_msg_id, current_text, is_from_forum_panel=True))

class AssigneeCloseView(discord.ui.View):
    def __init__(self, target_channel, ticket_msg_id):
        super().__init__(timeout=None)
        self.target_channel = target_channel
        self.ticket_msg_id = ticket_msg_id

    @discord.ui.button(label="完了にする", style=discord.ButtonStyle.primary)
    async def complete(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        gid, cid = str(itx.guild_id), str(self.target_channel.id)
        
        # Check uncompleted tasks
        if cid in cog.db.timers.get(gid, {}):
            tasks = cog.db.timers[gid][cid].get("tasks", {}).get(str(self.ticket_msg_id), [])
            uncompleted = [t["name"] for t in tasks if not t["completed"]]
            
            if uncompleted:
                embed = discord.Embed(title="⚠️ 未完了のタスクがあります", description="以下のタスクが残っています。強制的に完了しますか？", color=discord.Color.orange())
                embed.add_field(name="残タスク", value="\n".join([f"・{n}" for n in uncompleted]))
                await itx.response.send_message(embed=embed, view=TaskForceCloseView(self.target_channel, self.ticket_msg_id), ephemeral=True)
                return

        await cog.close_ticket(self.target_channel, itx.user, self.ticket_msg_id)
        await itx.response.send_message("✅ 完了しました。", ephemeral=True)

    @discord.ui.button(label="チャンネル削除", style=discord.ButtonStyle.danger)
    async def delete_ch(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        await cog.log_to_forum(self.target_channel, content="🗑️ 手動削除されました。", close_thread=True)
        gid, cid = str(itx.guild_id), str(self.target_channel.id)
        if cid in cog.db.timers.get(gid, {}):
            del cog.db.timers[gid][cid]
            cog.db.save_timers()
        await itx.response.send_message("削除します...", ephemeral=True)
        await asyncio.sleep(2)
        try:
            await self.target_channel.delete()
        except discord.NotFound:
            pass

class TaskForceCloseView(discord.ui.View):
    def __init__(self, target_channel, ticket_msg_id):
        super().__init__(timeout=180)
        self.target_channel = target_channel
        self.ticket_msg_id = ticket_msg_id

    @discord.ui.button(label="⚠️ 強制クローズ", style=discord.ButtonStyle.danger)
    async def force_close(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        await cog.close_ticket(self.target_channel, itx.user, self.ticket_msg_id)
        await itx.response.send_message("✅ 強制完了しました。", ephemeral=True)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.send_message("キャンセルしました。", ephemeral=True)

class MyDashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="受付切替 (Toggle)", style=discord.ButtonStyle.primary, custom_id="my_dash_toggle")
    async def toggle(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        await cog.toggle_reception(itx)
        new_member = await itx.guild.fetch_member(itx.user.id)
        embed = await cog.create_my_dashboard_embed(itx.guild, new_member)
        await itx.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📝 テンプレート編集", style=discord.ButtonStyle.secondary)
    async def tmpl(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        p = cog.db.get_user_profile(itx.guild_id, itx.user.id)
        await itx.response.send_modal(ProfileTemplateModal(p.get("template", "")))

class AdminDashboardView(discord.ui.View):
    def __init__(self, cog=None, guild=None): 
        super().__init__(timeout=180)
        self.cog = cog
        self.guild = guild
        if cog and guild:
            self.add_item(AdminAssigneeSelect(cog, guild))

    @discord.ui.button(label="📝 共通テンプレート編集", style=discord.ButtonStyle.secondary, row=1)
    async def tmpl(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        g = cog.db.get_guild_config(itx.guild_id)
        await itx.response.send_modal(GlobalTemplateModal(g.get("template", "")))

    @discord.ui.button(label="🔄 更新", style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("Tickets")
        embed = await cog.create_admin_dashboard_embed(itx.guild)
        await itx.response.edit_message(embed=embed, view=AdminDashboardView(cog, itx.guild))

class AdminAssigneeSelect(discord.ui.Select):
    def __init__(self, cog, guild):
        options = cog.get_assignee_options(guild, sort_key=None)
        if not options:
            options = [discord.SelectOption(label="担当者がいません", value="none", default=True)]
        super().__init__(placeholder="🔍 詳細を確認する担当者を選択...", min_values=1, max_values=1, options=options[:25], row=0)

    async def callback(self, itx: discord.Interaction):
        if self.values[0] == "none":
            return
        cog = itx.client.get_cog("Tickets")
        target_id = int(self.values[0])
        target = itx.guild.get_member(target_id)
        name = target.display_name if target else f"Unknown({target_id})"
        embed = await cog.create_assignee_detail_embed(itx.guild, target_id, name)
        await itx.response.edit_message(embed=embed, view=AdminAssigneeDetailView(cog, itx.guild))

class AdminAssigneeDetailView(discord.ui.View):
    def __init__(self, cog, guild):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild = guild

    @discord.ui.button(label="◀ 戻る", style=discord.ButtonStyle.secondary)
    async def back(self, itx: discord.Interaction, button: discord.ui.Button):
        embed = await self.cog.create_admin_dashboard_embed(self.guild)
        await itx.response.edit_message(embed=embed, view=AdminDashboardView(self.cog, self.guild))

class TimerEditModal(discord.ui.Modal, title="タイマー設定"):
    def __init__(self, h, d, target_channel):
        super().__init__()
        self.target_channel = target_channel
        self.h = discord.ui.TextInput(label="リマインド(h)", default=str(h))
        self.d = discord.ui.TextInput(label="自動クローズ(day)", default=str(d))
        self.add_item(self.h)
        self.add_item(self.d)

    async def on_submit(self, itx: discord.Interaction):
        try:
            h, d = int(self.h.value), int(self.d.value)
        except:
            await itx.response.send_message("数値エラー", ephemeral=True)
            return
        cog = itx.client.get_cog("Tickets")
        gid, cid = str(itx.guild_id), str(self.target_channel.id)
        if cid in cog.db.timers.get(gid, {}):
            cog.db.timers[gid][cid].update({"timeout_hours": h, "auto_close_days": d, "last_message_at": datetime.datetime.now().isoformat(), "reminded": False})
            cog.db.save_timers()
            await itx.response.send_message("✅ 設定更新＆タイマー再開", ephemeral=True)

class SubmitUrlModalExt(discord.ui.Modal, title="提出先URL"):
    url = discord.ui.TextInput(label="URL", max_length=200)

    def __init__(self, target_channel):
        super().__init__()
        self.target_channel = target_channel

    async def on_submit(self, itx: discord.Interaction):
        target_msg = None
        async for msg in self.target_channel.history(limit=20):
            if msg.author.id == itx.client.user.id and msg.embeds and msg.embeds[0].color != discord.Color.dark_grey():
                target_msg = msg
                break
        if not target_msg:
            await itx.response.send_message("対象なし", ephemeral=True)
            return
        embed = target_msg.embeds[0]
        new_field = {"name": "📂 提出先", "value": f"[Link]({self.url.value})\n`{self.url.value}`", "inline": False}
        fds = [{"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields]
        updated = False
        for i, f in enumerate(fds):
            if "提出先" in f["name"]:
                fds[i] = new_field
                updated = True
                break
        if not updated:
            fds.insert(2, new_field)
        embed.clear_fields()
        for f in fds:
            embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
        await target_msg.edit(embed=embed)
        cog = itx.client.get_cog("Tickets")
        await cog.log_to_forum(self.target_channel, content=f"📂 提出先設定: {self.url.value}")
        await itx.response.send_message("更新しました", ephemeral=True)

class ProfileTemplateModal(discord.ui.Modal, title="テンプレート編集"):
    def __init__(self, current):
        super().__init__()
        self.c = discord.ui.TextInput(label="内容", style=discord.TextStyle.paragraph, default=current, required=False)
        self.add_item(self.c)

    async def on_submit(self, itx): 
        cog = itx.client.get_cog("Tickets")
        p = cog.db.get_user_profile(itx.guild_id, itx.user.id)
        p["template"] = cog.resolve_mentions(itx.guild, self.c.value)
        cog.db.save_profiles()
        await itx.response.send_message("更新しました", ephemeral=True)

class GlobalTemplateModal(discord.ui.Modal, title="共通テンプレート編集"):
    def __init__(self, current):
        super().__init__()
        self.c = discord.ui.TextInput(label="内容", style=discord.TextStyle.paragraph, default=current, required=False)
        self.add_item(self.c)

    async def on_submit(self, itx): 
        cog = itx.client.get_cog("Tickets")
        g = cog.db.get_guild_config(itx.guild_id)
        g["template"] = cog.resolve_mentions(itx.guild, self.c.value)
        cog.db.save_profiles()
        await itx.response.send_message("更新しました", ephemeral=True)

@persistent_view
class AutoCloseConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🗑️ 削除する", style=discord.ButtonStyle.danger, custom_id="ac_del")
    async def delete(self, itx: discord.Interaction, btn: discord.ui.Button):
        cid = str(itx.channel.id)
        gid = str(itx.guild_id)
        cog = itx.client.get_cog("Tickets")
        ch = itx.guild.get_channel(int(cid))
        if ch: 
            await cog.log_to_forum(ch, content="🗑️ 自動削除を実行しました。", close_thread=True)
            if cid in cog.db.timers.get(gid, {}): 
                del cog.db.timers[gid][cid]
                cog.db.save_timers()
            await ch.delete()
        else:
            if cid in cog.db.timers.get(gid, {}): 
                del cog.db.timers[gid][cid]
                cog.db.save_timers()

    @discord.ui.button(label="延長 (まだ使う)", style=discord.ButtonStyle.success, custom_id="ac_ext")
    async def extend(self, itx: discord.Interaction, btn: discord.ui.Button): 
        cid = str(itx.channel.id)
        gid = str(itx.guild_id)
        cog = itx.client.get_cog("Tickets")
        if cid in cog.db.timers.get(gid, {}): 
            cog.db.timers[gid][cid].update({"last_message_at": datetime.datetime.now().isoformat(), "close_confirming": False})
            cog.db.save_timers()
        await itx.message.delete()
        await itx.response.send_message(f"✅ タイマーを延長しました。", ephemeral=True)

@persistent_view
class ReminderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="確認 (タイマー延長)", style=discord.ButtonStyle.primary, custom_id="rem_ext")
    async def extend(self, itx: discord.Interaction, btn: discord.ui.Button):
        cid = str(itx.channel.id)
        gid = str(itx.guild_id)
        cog = itx.client.get_cog("Tickets")
        if cid in cog.db.timers.get(gid, {}): 
            cog.db.timers[gid][cid].update({"last_message_at": datetime.datetime.now().isoformat(), "reminded": False})
            cog.db.save_timers()
        await itx.message.delete()
        await itx.response.send_message(f"✅ タイマーを延長しました。", ephemeral=True)

@persistent_view
class ReopenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔄 再開", style=discord.ButtonStyle.primary, custom_id="reopen_ticket")
    async def reopen(self, itx, btn):
        cog = itx.client.get_cog("Tickets")
        embed = itx.message.embeds[0]
        embed.color = discord.Color.blue()
        embed.title = embed.title.replace("✅ [完了] ", "")
        await itx.message.edit(embed=embed, view=TicketControlView())
        gid, cid = str(itx.guild_id), str(itx.channel.id)
        if cid in cog.db.timers.get(gid, {}):
            at = cog.db.timers[gid][cid].get("active_tickets", [])
            if itx.message.id not in at:
                at.append(itx.message.id)
            cog.db.timers[gid][cid].update({"active_tickets": at, "last_message_at": datetime.datetime.now().isoformat(), "reminded": False})
            cog.db.save_timers()
        await cog.log_to_forum(itx.channel, content="🔄 **再開されました**")
        await itx.response.send_message("再開しました", ephemeral=True)

# ====================================================
# Cog Logic
# ====================================================
class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = TicketDataManager()
        self.check_inactivity_loop.start()
        self.autosave_loop.start()

    def cog_unload(self):
        self.check_inactivity_loop.cancel()
        self.autosave_loop.cancel()
        self.db.flush()

    @tasks.loop(seconds=60)
    async def autosave_loop(self):
        self.db.flush()

    def resolve_mentions(self, guild: discord.Guild, text: str) -> Optional[str]:
        if not text:
            return None
        def replacer(match):
            symbol, name = match.group(1), match.group(2)
            if symbol == "#":
                ch = discord.utils.find(lambda c: c.name.lower() == name.lower(), guild.text_channels)
                return ch.mention if ch else match.group(0)
            elif symbol == "@":
                role = discord.utils.find(lambda r: r.name.lower() == name.lower(), guild.roles)
                return role.mention if role else match.group(0)
            return match.group(0)
        return re.sub(r"([#@])([^\s　]+)", replacer, text)

    def get_assignee_options(self, guild: discord.Guild, sort_key: str = None) -> List[discord.SelectOption]:
        g_conf = self.db.get_guild_config(guild.id)
        a_rid = g_conf.get("assignee_role_id")
        q_rid = g_conf.get("assignee_qual_role_id")
        a_role = guild.get_role(a_rid) if a_rid else None
        q_role = guild.get_role(q_rid) if q_rid else None
        if not a_role and not q_role:
            return []
        
        target_members = set()
        if a_role:
            target_members.update(a_role.members)
        if q_role:
            target_members.update(q_role.members)
        
        is_desc = True 
        if sort_key:
            attr_def = g_conf.get("attributes", {}).get(sort_key, {})
            if attr_def.get("order") == "asc":
                is_desc = False

        member_list = []
        for member in target_members:
            if member.bot:
                continue
            p = self.db.get_user_profile(guild.id, member.id)
            val = p.get("attributes", {}).get(sort_key, 0) if sort_key else 0
            if sort_key and val == 0 and not is_desc:
                val = 99999999
            member_list.append({"member": member, "val": val})
        
        member_list.sort(key=lambda x: (x["val"] if not is_desc else -x["val"]))
        options = []
        for s in member_list:
            member = s["member"]
            desc_text = f"{member.display_name} さん"
            if sort_key:
                raw_val = s["val"]
                val_str = raw_val if raw_val != 99999999 else '-'
                desc_text = f"[{sort_key}: {val_str}]"
            status_mark = "🟢" if (a_role and a_role in member.roles) else "💤"
            options.append(discord.SelectOption(label=member.display_name, value=str(member.id), description=f"{status_mark} {desc_text}", emoji="👤"))
        return options

    def _get_setting(self, guild_id, profile, key, system_default):
        val = profile.get(key)
        if val is not None:
            return val
        g_conf = self.db.get_guild_config(guild_id)
        return g_conf.get(key, system_default)

    def check_accept_status(self, guild, assignee, creator):
        g_conf = self.db.get_guild_config(guild.id)
        rid = g_conf.get("assignee_role_id")
        if rid:
            role = guild.get_role(rid)
            if role and role not in assignee.roles:
                return "⚠️ 現在、この担当者は受付を停止しています (休憩中)。"
        p = self.db.get_user_profile(guild.id, assignee.id)
        has_category = p.get("category_id") or g_conf.get("category_id")
        if not (has_category or bool(p.get("attributes"))):
            return f"⚠️ {assignee.display_name} さんは、受付設定が未完了です。"
        if creator.id in p.get("blacklist", []):
            return "⛔ 受付不可 (BL)"
        
        max_s = p.get("max_slots") or g_conf.get("max_slots", DEFAULT_MAX_SLOTS)
        current_user_tickets = 0
        gid = str(guild.id)
        for t in self.db.timers.get(gid, {}).values():
            if t.get("assignee_id") == assignee.id and t.get("creator_id") == creator.id and t.get("active_tickets"):
                current_user_tickets += len(t.get("active_tickets", []))
        if current_user_tickets >= max_s:
            return f"⛔ あなたは既に {current_user_tickets}件 依頼中です。(上限: {max_s}件)"
        return None
    
    def _update_settings_logic(self, data: dict, is_guild: bool, **kwargs):
        msg = []
        for arg, val in kwargs.items():
            if val is None or arg in ["mention_role", "reset_roles", "ignore_role", "log_role"]:
                continue
            db_key, store_val, display_val = arg, val, val
            if hasattr(val, "id"):
                db_key = f"{arg}_id"
                store_val = val.id
                display_val = val.name if hasattr(val, "name") else val.mention
            data[db_key] = store_val
            label = arg.replace("_", " ").title()
            msg.append(f"{label}: {display_val}")
        
        list_key = "mention_roles"
        toggle_role = kwargs.get("mention_role")
        reset = kwargs.get("reset_roles")
        if reset:
            data[list_key] = [] if is_guild else None
            msg.append("Mentions: 🔄 Reset")
        if toggle_role:
            current_list = data.get(list_key) or []
            if toggle_role.id in current_list:
                current_list.remove(toggle_role.id)
                msg.append(f"Mentions: ➖ Remove {toggle_role.name}")
            else:
                current_list.append(toggle_role.id)
                msg.append(f"Mentions: ➕ Add {toggle_role.name}")
            data[list_key] = current_list

        ignore_key = "ignore_roles"
        toggle_ignore = kwargs.get("ignore_role")
        if toggle_ignore:
            current_ignore = data.get(ignore_key) or []
            if toggle_ignore.id in current_ignore:
                current_ignore.remove(toggle_ignore.id)
                msg.append(f"Ignore: ➖ Remove {toggle_ignore.name}")
            else:
                current_ignore.append(toggle_ignore.id)
                msg.append(f"Ignore: ➕ Add {toggle_ignore.name}")
            data[ignore_key] = current_ignore

        log_key = "log_roles"
        toggle_log = kwargs.get("log_role")
        if toggle_log:
            current_log = data.get(log_key) or []
            if toggle_log.id in current_log:
                current_log.remove(toggle_log.id)
                msg.append(f"Log Mention: ➖ Remove {toggle_log.name}")
            else:
                current_log.append(toggle_log.id)
                msg.append(f"Log Mention: ➕ Add {toggle_log.name}")
            data[log_key] = current_log

        return msg

    async def create_ticket_entry(self, guild, creator, assignee, creator_name, title, c_type, deadline, budget):
        # Ensure creator is a Member object for correct mention formatting
        if isinstance(creator, (discord.User, discord.Object)):
            creator = guild.get_member(creator.id) or creator
            
        p = self.db.get_user_profile(guild.id, assignee.id)
        reuse = self._get_setting(guild.id, p, "reuse_channel", DEFAULT_REUSE_CHANNEL)
        target_channel = None
        gid = str(guild.id)
        if reuse:
            for cid, data in self.db.timers.get(gid, {}).items():
                if data.get("assignee_id") == assignee.id and data.get("creator_id") == creator.id:
                    ch = guild.get_channel(int(cid))
                    if ch:
                        target_channel = ch
                        break
        if not target_channel:
            target_channel = await self._create_new_channel(guild, creator, assignee, p, title)
        
        mentions = [assignee.mention]
        t_rids = p.get("mention_roles")
        if not t_rids:
            t_rids = self.db.get_guild_config(guild.id).get("mention_roles", [])
        for rid in t_rids:
            r = guild.get_role(rid)
            if r and r.mention not in mentions:
                mentions.append(r.mention)
            
        tmpl = p.get("template") or self.db.get_guild_config(guild.id).get("template")
        desc_head = ""
        if tmpl:
            desc_head = tmpl.replace("{creator}", creator.mention).replace("{user}", creator.mention).replace("{creator_name}", creator_name).replace("{assignee}", assignee.mention).replace("{title}", title).replace("\\n", "\n") + "\n\n"
        
        embed = discord.Embed(title=f"案件: {title}", description=f"{desc_head}担当: {assignee.mention}", color=discord.Color.blue(), timestamp=datetime.datetime.now())
        embed.add_field(name="👤 依頼者", value=f"{creator.mention}\n(名義: **{creator_name}**)", inline=True)
        embed.add_field(name="📋 依頼形態", value=c_type, inline=True)
        embed.add_field(name="📅 希望納期", value=deadline, inline=True)
        if budget:
            embed.add_field(name="💰 予算", value=budget, inline=True)
        embed.add_field(name="📂 提出先", value="🚫 **未設定**", inline=False)
        embed.add_field(name="⚠️ 次のステップ", value="下の **「🎵 詳細入力」** ボタンを押して、内容を入力してください。", inline=False)
        
        msg = await target_channel.send(content=" ".join(mentions), embed=embed, view=TicketControlView())
        cd = self.db.timers[gid][str(target_channel.id)]
        if "tasks" not in cd:
            cd["tasks"] = {}
        cd["tasks"][str(msg.id)] = []
        
        cd["active_tickets"].append(msg.id)
        cd["last_message_at"] = datetime.datetime.now().isoformat()
        cd["reminded"] = False
        self.db.save_timers()
        await self._init_forum_thread(target_channel, embed, p, mentions)
        return target_channel, msg

    async def _create_new_channel(self, guild, creator, assignee, profile, title):
        cat_id = profile.get("category_id") or self.db.get_guild_config(guild.id).get("category_id")
        category = guild.get_channel(cat_id) if cat_id else None
        date_str = datetime.datetime.now().strftime("%y%m%d")
        safe_title = title.replace(" ", "_").lower()[:10]
        fmt = profile.get("name_format") or self.db.get_guild_config(guild.id).get("name_format") or "{creator}"
        ch_name = fmt.format(date=date_str, creator=creator.name.lower(), assignee=assignee.name.lower(), title=safe_title, id=creator.id, assignee_id=assignee.id)
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            creator: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            assignee: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, manage_channels=True)
        }
        m_rids = profile.get("mention_roles")
        if not m_rids:
            m_rids = self.db.get_guild_config(guild.id).get("mention_roles", [])
        for rid in m_rids:
            r = guild.get_role(rid)
            if r:
                overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            
        channel = await guild.create_text_channel(name=ch_name, category=category, overwrites=overwrites)
        self.db.timers[str(guild.id)][str(channel.id)] = {
            "last_message_at": datetime.datetime.now().isoformat(),
            "enabled": self._get_setting(guild.id, profile, "notify_enabled", DEFAULT_NOTIFY_ENABLED),
            "timeout_hours": self._get_setting(guild.id, profile, "timeout_hours", DEFAULT_TIMEOUT_HOURS),
            "assignee_id": assignee.id, "creator_id": creator.id, "active_tickets": [],
            "auto_close_enabled": self._get_setting(guild.id, profile, "auto_close_enabled", DEFAULT_AUTO_CLOSE_ENABLED),
            "auto_close_days": self._get_setting(guild.id, profile, "auto_close_days", DEFAULT_AUTO_CLOSE_DAYS),
            "mirror_thread_id": None, "last_log_at": None, "tasks": {}
        }
        self.db.save_timers()
        return channel

    async def _init_forum_thread(self, channel, embed, profile, mentions):
        fid = profile.get("transcript_id") or self.db.get_guild_config(channel.guild.id).get("transcript_id")
        if not fid:
            return 
        forum = channel.guild.get_channel(fid)
        if not isinstance(forum, discord.ForumChannel):
            return
        gid, cid = str(channel.guild.id), str(channel.id)
        t_data = self.db.timers.get(gid, {}).get(cid, {})
        thread = None
        created_new = False

        if t_data.get("mirror_thread_id"):
            try:
                thread = await channel.guild.fetch_channel(t_data["mirror_thread_id"])
            except:
                pass 

        if not thread:
            candidates = []
            try: 
                for t in forum.threads:
                    candidates.append(t)
                async for t in forum.archived_threads(limit=50):
                    candidates.append(t)
            except:
                pass
            for t in candidates:
                if t.name == channel.name:
                    thread = t
                    self.db.timers[gid][cid]["mirror_thread_id"] = thread.id
                    self.db.save_timers()
                    break

        if not thread:
            try:
                mention_str = " ".join(mentions) if mentions else ""
                t_w_msg = await forum.create_thread(name=channel.name, content=f"🆕 **New Ticket Log Created** (Source: {channel.mention})\n{mention_str}", embed=embed)
                thread = t_w_msg.thread
                self.db.timers[gid][cid]["mirror_thread_id"] = thread.id
                self.db.save_timers()
            except:
                return
        else:
            if thread.archived:
                await thread.edit(archived=False)

        if mentions and not created_new:
            await thread.send(content=f"🔔 **Notification:** {' '.join(mentions)}")

    async def log_to_forum(self, channel, content=None, embed=None, attachments=None, is_update=False, close_thread=False, view=None, target_msg_id=None):
        gid, cid = str(channel.guild.id), str(channel.id)
        if cid not in self.db.timers.get(gid, {}):
            return
        t_data = self.db.timers[gid][cid]
        tid = t_data.get("mirror_thread_id")
        if not tid:
            return
        try:
            thread = await channel.guild.fetch_channel(tid)
        except:
            return 
        if not attachments and not is_update and not close_thread and not view:
            last_log, assignee_id = t_data.get("last_log_at"), t_data.get("assignee_id")
            prof = self.db.get_user_profile(channel.guild.id, assignee_id) if assignee_id else {}
            cooldown = self._get_setting(channel.guild.id, prof, "cooldown", DEFAULT_LOG_COOLDOWN)
            if last_log and (datetime.datetime.now() - datetime.datetime.fromisoformat(last_log)).total_seconds() < cooldown:
                return 
        final_content = content
        if not close_thread:
            aid = t_data.get("assignee_id")
            g_conf = self.db.get_guild_config(channel.guild.id)
            m_list = [] 
            
            base_rids = None
            log_rids = None
            if aid:
                p = self.db.get_user_profile(channel.guild.id, aid)
                base_rids = p.get("mention_roles")
                log_rids = p.get("log_roles")
            if not base_rids:
                base_rids = g_conf.get("mention_roles", [])
            if not log_rids:
                log_rids = g_conf.get("log_roles", [])
            
            all_rids = list(set((base_rids or []) + (log_rids or [])))

            for rid in all_rids:
                if f"<@&{rid}>" not in m_list:
                    m_list.append(f"<@&{rid}>")
            if view:
                mention_str = " ".join(m_list)
                final_content = f"{mention_str}\n{content}" if content else mention_str
            elif m_list:
                mention_str = " ".join(m_list)
                final_content = f"{mention_str}\n{content}" if content else mention_str

        # Append Task List if exists
        task_view = None
        ticket_msg_id = target_msg_id
        if ticket_msg_id is None and t_data.get("active_tickets"):
            ticket_msg_id = t_data["active_tickets"][-1]
            
        task_list = t_data.get("tasks", {}).get(str(ticket_msg_id), []) if ticket_msg_id else []
        if task_list and not close_thread:
            task_str = "\n──────────────\n**📋 タスクリスト**\n"
            for t in task_list:
                mark = "✅" if t["completed"] else "☑️"
                task_str += f"{mark} {t['name']}\n"
            
            if embed:
                # Add to description or last field if possible, or new field?
                # User preference: "Embedの末尾（または description の最後）"
                if embed.description:
                    embed.description += task_str
                else:
                    embed.description = task_str
            else:
                # If no embed, make one? Or append to content.
                # Usually logs have embeds or content. If only content, append to content.
                if final_content:
                    final_content += task_str
                else:
                    final_content = task_str
            
            # Use ForumTaskLogView if not overriding view
            if view is None:
                task_view = ForumTaskLogView()
        
        # Determine final view
        final_view = view if view else task_view

        files = [await a.to_file() for a in attachments] if attachments else []
        try:
            await thread.send(content=final_content, embed=embed, files=files, view=final_view)
            t_data["last_log_at"] = datetime.datetime.now().isoformat()
            self.db.save_timers()
            if close_thread:
                await thread.edit(archived=True, locked=True)
        except Exception as e:
            logger.error(f"Log Error: {e}")

    async def close_ticket(self, channel, user, ticket_msg_id=None):
        gid, cid = str(channel.guild.id), str(channel.id)
        if cid not in self.db.timers.get(gid, {}):
            return
        active_tickets = self.db.timers[gid][cid].get("active_tickets", [])
        to_close = [ticket_msg_id] if ticket_msg_id else active_tickets.copy()
        for msg_id in to_close:
            try:
                msg = await channel.fetch_message(msg_id)
                if msg and msg.embeds:
                    embed = msg.embeds[0]
                    embed.title = f"✅ [完了] {embed.title}"
                    embed.color = discord.Color.grey()
                    await msg.edit(embed=embed, view=ReopenView())
            except:
                pass
            if msg_id in active_tickets:
                active_tickets.remove(msg_id)
        self.db.timers[gid][cid]["active_tickets"] = active_tickets
        self.db.save_timers()
        await self.log_to_forum(channel, content=f"✅ **{user.display_name} によって完了とマークされました**", close_thread=(len(active_tickets) == 0))

    async def toggle_reception(self, interaction: discord.Interaction):
        g_conf = self.db.get_guild_config(interaction.guild_id)
        role_id = g_conf.get("assignee_role_id")
        if not role_id:
            await interaction.response.send_message("設定エラー: Assignee Role が設定されていません。", ephemeral=True)
            return
        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message("設定エラー: ロールが見つかりません。", ephemeral=True)
            return
        user = interaction.user
        if role in user.roles:
            await user.remove_roles(role)
            await interaction.response.send_message("💤 受付を停止しました。", ephemeral=True)
        else:
            await user.add_roles(role)
            await interaction.response.send_message("🟢 受付を開始しました。", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        gid, cid = str(message.guild.id), str(message.channel.id)
        if cid in self.db.timers.get(gid, {}):
            self.db.timers[gid][cid].update({"last_message_at": datetime.datetime.now().isoformat(), "reminded": False, "close_confirming": False})
            self.db.save_timers()
            
            g_conf = self.db.get_guild_config(message.guild.id)
            ignore_rids = g_conf.get("ignore_roles", []) or []

            t_data = self.db.timers.get(gid, {}).get(cid, {})
            aid = t_data.get("assignee_id")
            if aid:
                p = self.db.get_user_profile(message.guild.id, aid)
                p_ignore = p.get("ignore_roles")
                if p_ignore:
                    ignore_rids = list(set(ignore_rids + p_ignore))

            if any(r.id in ignore_rids for r in message.author.roles):
                return

            desc = f"{message.content}\n\n🔗 [Jump]({message.jump_url})"
            e = discord.Embed(description=desc, timestamp=message.created_at)
            e.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
            await self.log_to_forum(message.channel, embed=e, attachments=message.attachments)

    @tasks.loop(minutes=10)
    async def check_inactivity_loop(self):
        await self.bot.wait_until_ready()
        now = datetime.datetime.now()
        for gid, guild_timers in list(self.db.timers.items()):
            for cid, info in list(guild_timers.items()):
                if not info.get("enabled", True) or not info.get("active_tickets") or not info.get("last_message_at"):
                    continue
                try:
                    last_msg_time = datetime.datetime.fromisoformat(info["last_message_at"])
                except ValueError:
                    continue
                delta = now - last_msg_time
                ch = self.bot.get_channel(int(cid))
                if not ch:
                    del self.db.timers[gid][cid]
                    self.db.save_timers()
                    continue
                if info.get("auto_close_enabled", True) and not info.get("close_confirming", False):
                    limit_days = info.get("auto_close_days", DEFAULT_AUTO_CLOSE_DAYS)
                    if delta > datetime.timedelta(days=limit_days):
                        view = AutoCloseConfirmView()
                        embed = discord.Embed(title="⚠️ 自動削除の確認", description=f"このチケットは {limit_days}日間 動きがありません。\n削除してもよろしいですか？", color=discord.Color.red())
                        embed.add_field(name="対象チャンネル", value=f"<#{cid}>")
                        await self.log_to_forum(ch, embed=embed, view=view)
                        info["close_confirming"] = True
                        self.db.save_timers()
                        continue
                if not info.get("reminded", False):
                    limit_hours = info.get("timeout_hours", DEFAULT_TIMEOUT_HOURS)
                    if delta > datetime.timedelta(hours=limit_hours):
                        view = ReminderView()
                        embed = discord.Embed(title="⏰ 未稼働通知", description=f"最後のメッセージから {limit_hours}時間 が経過しました。\n進行状況を確認してください。", color=discord.Color.orange())
                        embed.add_field(name="対象チャンネル", value=f"<#{cid}>")
                        await self.log_to_forum(ch, embed=embed, view=view)
                        info["reminded"] = True
                        self.db.save_timers()

    async def create_my_dashboard_embed(self, guild, user):
        p = self.db.get_user_profile(guild.id, user.id)
        g = self.db.get_guild_config(guild.id)
        t_rid = g.get("assignee_role_id")
        t_role = guild.get_role(t_rid) if t_rid else None
        status = "🟢 Accepting" if (t_role and t_role in user.roles) else "💤 Not Accepting"
        def show(key, unit=""):
            u, gv = p.get(key), g.get(key)
            return f"**{u}{unit}** (個人)" if u is not None else f"{gv}{unit} (Default)"
        embed = discord.Embed(title=f"👤 My Dashboard: {user.display_name}", color=discord.Color.green())
        embed.add_field(name="Status", value=status, inline=False)
        embed.add_field(name="Limit/user", value=show("max_slots"), inline=True)
        embed.add_field(name="Reuse", value=show("reuse_channel"), inline=True)
        embed.add_field(name="Timer", value=show("timeout_hours", "h"), inline=True)
        m_roles = p.get("mention_roles")
        if not m_roles:
            m_roles = g.get("mention_roles", [])
        m_str = ", ".join([guild.get_role(r).name for r in m_roles if guild.get_role(r)]) or "なし"
        l_roles = p.get("log_roles")
        if not l_roles:
            l_roles = g.get("log_roles", [])
        l_str = ", ".join([guild.get_role(r).name for r in l_roles if guild.get_role(r)]) or "なし"
        embed.add_field(name="Mentions", value=f"Ticket: {m_str}\nLog+: {l_str}", inline=False)
        return embed

    async def create_admin_dashboard_embed(self, guild):
        g = self.db.get_guild_config(guild.id)
        gid = str(guild.id)
        embed = discord.Embed(title="🛡️ Admin Dashboard (Full View)", description="下のメニューから担当者を選択して詳細設定を確認できます。", color=discord.Color.gold())
        def r_name(rid):
            r = guild.get_role(rid)
            return r.mention if r else "❌ 未設定"
        def on_off(val):
            return "✅ ON" if val else "❌ OFF"
        embed.add_field(name="🔑 Roles", value=f"Assignee: {r_name(g.get('assignee_role_id'))}\nQual: {r_name(g.get('assignee_qual_role_id'))}", inline=True)
        lfid = g.get("transcript_id")
        lf = guild.get_channel(lfid) if lfid else None
        embed.add_field(name="📜 Logs", value=f"Channel: {lf.mention if lf else '❌ 未設定'}\nCooldown: {g.get('cooldown')}s", inline=True)
        embed.add_field(name="⚙️ Behaviors", value=f"Reuse Channel: {on_off(g.get('reuse_channel'))}\nNotify Enabled: {on_off(g.get('notify_enabled'))}\nFormat: `{g.get('name_format', 'Default')}`", inline=False)
        ac_days = f"{g.get('auto_close_days')}d" if g.get("auto_close_enabled") else "❌ Disabled"
        embed.add_field(name="⏱️ Timers & Limits", value=f"Timeout: {g.get('timeout_hours')}h\nAuto Close: {ac_days}\nMax Slots/User: {g.get('max_slots')}", inline=False)
        m_roles = g.get("mention_roles", [])
        m_str = ", ".join([guild.get_role(r).mention for r in m_roles if guild.get_role(r)]) or "なし"
        l_roles = g.get("log_roles", [])
        l_str = ", ".join([guild.get_role(r).mention for r in l_roles if guild.get_role(r)]) or "なし"
        i_roles = g.get("ignore_roles", [])
        i_str = ", ".join([guild.get_role(r).mention for r in i_roles if guild.get_role(r)]) or "なし"
        attr_list = list(g.get("attributes", {}).keys())
        attr_str = ", ".join(attr_list) if attr_list else "なし"
        embed.add_field(name="🔔 Mentions", value=m_str, inline=True)
        embed.add_field(name="📢 Log Extra", value=l_str, inline=True)
        embed.add_field(name="🚫 Ignore Log", value=i_str, inline=True)
        embed.add_field(name="🏷️ Attributes", value=attr_str, inline=False)
        
        # Category
        cat_id = g.get("category_id")
        cat = guild.get_channel(cat_id) if cat_id else None
        embed.add_field(name="📂 Default Category", value=cat.name if cat else "なし", inline=True)

        a_rid = g.get("assignee_role_id")
        q_rid = g.get("assignee_qual_role_id")
        a_role = guild.get_role(a_rid) if a_rid else None
        q_role = guild.get_role(q_rid) if q_rid else None
        target_members = set()
        if a_role:
            target_members.update(a_role.members)
        if q_role:
            target_members.update(q_role.members)
        target_members = [m for m in target_members if not m.bot]
        if target_members:
            accepting_count = len([m for m in target_members if a_role and a_role in m.roles])
            total_count = len(target_members)
            embed.add_field(name="Assignee Stats", value=f"Accepting: **{accepting_count}** / Total: **{total_count}**", inline=False)
            text_lines = []
            for member in target_members:
                p = self.db.get_user_profile(guild.id, member.id)
                status_icon = "🟢" if (a_role and a_role in member.roles) else "💤"
                active = 0
                if gid in self.db.timers:
                    for t in self.db.timers[gid].values():
                        if int(t.get("assignee_id", 0)) == member.id and t.get("active_tickets"):
                            active += len(t.get("active_tickets", []))
                max_s = p.get("max_slots") or g.get("max_slots", DEFAULT_MAX_SLOTS)
                text_lines.append(f"{status_icon} **{member.display_name}** | Act: **{active}** | Lim: {max_s}")
            chunk = ""
            for line in text_lines:
                if len(chunk) + len(line) > 1000:
                    embed.add_field(name="👥 Assignees", value=chunk, inline=False)
                    chunk = ""
                chunk += line + "\n"
            if chunk:
                embed.add_field(name="👥 Assignees", value=chunk, inline=False)
        else:
            embed.add_field(name="👥 Assignees", value="メンバーなし", inline=False)
        return embed

    async def create_assignee_detail_embed(self, guild, member_id, name):
        p = self.db.get_user_profile(guild.id, member_id)
        g = self.db.get_guild_config(guild.id)
        embed = discord.Embed(title=f"👤 Assignee Profile: {name}", color=discord.Color.blue())
        def val_str(key, unit="", transform=lambda x: x):
            pv = p.get(key)
            gv = g.get(key)
            if pv is not None:
                return f"**{transform(pv)}{unit}** (Custom)"
            return f"{transform(gv)}{unit} (Default)"
        def on_off(v):
            return "ON" if v else "OFF"
        embed.add_field(name="⚙️ Config", value=f"Reuse Channel: {val_str('reuse_channel', transform=on_off)}\nNotify: {val_str('notify_enabled', transform=on_off)}\nFormat: `{p.get('name_format') or g.get('name_format')}`", inline=False)
        embed.add_field(name="⏱️ Limits", value=f"Max Slots: {val_str('max_slots')}\nTimeout: {val_str('timeout_hours', 'h')}\nAutoClose: {val_str('auto_close_days', 'd')}", inline=False)
        pm = p.get("mention_roles")
        gm = g.get("mention_roles", [])
        m_list = pm if pm is not None else gm
        m_str = ", ".join([guild.get_role(r).mention for r in m_list if guild.get_role(r)]) or "なし"
        m_label = "(Custom)" if pm is not None else "(Default)"
        embed.add_field(name=f"🔔 Mentions {m_label}", value=m_str, inline=False)

        pl = p.get("log_roles")
        gl = g.get("log_roles", [])
        l_list = pl if pl is not None else gl
        l_str = ", ".join([guild.get_role(r).mention for r in l_list if guild.get_role(r)]) or "なし"
        l_label = "(Custom)" if pl is not None else "(Default)"
        embed.add_field(name=f"📢 Log Extra {l_label}", value=l_str, inline=False)
        
        pi = p.get("ignore_roles")
        gi = g.get("ignore_roles", [])
        i_list = pi if pi is not None else gi
        i_str = ", ".join([guild.get_role(r).mention for r in i_list if guild.get_role(r)]) or "なし"
        i_label = "(Custom)" if pi is not None else "(Default)"
        embed.add_field(name=f"🚫 Ignore {i_label}", value=i_str, inline=False)
        
        # Category
        pc = p.get("category_id")
        gc = g.get("category_id")
        c_id = pc if pc else gc
        cat = guild.get_channel(c_id) if c_id else None
        c_label = "(Custom)" if pc else "(Default)"
        embed.add_field(name=f"📂 Category {c_label}", value=cat.name if cat else "なし", inline=False)

        attrs = p.get("attributes", {})
        attr_str = "\n".join([f"{k}: {v}" for k, v in attrs.items()]) or "なし"
        embed.add_field(name="🏷️ Attributes", value=attr_str, inline=True)
        tmpl = "設定あり" if p.get("template") else "なし (Default)"
        embed.add_field(name="📝 Template", value=tmpl, inline=True)
        return embed

    async def create_ticket_dashboard_embed(self, channel, t_data):
        last = datetime.datetime.fromisoformat(t_data["last_message_at"])
        delta = datetime.datetime.now() - last
        embed = discord.Embed(title=f"⏱️ Manager: {channel.name}", color=discord.Color.light_grey())
        status = "✅ 稼働中"
        if t_data.get("reminded"):
            status = "⏰ 通知済み"
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Setting", value=f"Limit: {t_data.get('timeout_hours')}h", inline=True)
        embed.add_field(name="Elapsed", value=f"{delta.total_seconds()/3600:.1f} hours", inline=False)
        tid = t_data.get("mirror_thread_id")
        embed.add_field(name="Transcript", value=f"<#{tid}>" if tid else "⚠️ 未連携", inline=False)
        return embed

    # ====================================================
    # Commands
    # ====================================================
    ticket_group = app_commands.Group(name="ticket", description="チケット管理")
    admin_group = app_commands.Group(name="admin", description="サーバー設定", parent=ticket_group)
    my_group = app_commands.Group(name="my", description="個人設定", parent=ticket_group)
    attr_group = app_commands.Group(name="attribute", description="属性管理", parent=ticket_group)

    @admin_group.command(name="setup", description="サーバー設定の変更")
    async def admin_setup(self, itx: discord.Interaction, category: Optional[discord.CategoryChannel] = None, assignee_role: Optional[discord.Role] = None, assignee_qual_role: Optional[discord.Role] = None, transcript: Optional[discord.ForumChannel] = None, timeout_hours: Optional[int] = None, auto_close_enabled: Optional[bool] = None, auto_close_days: Optional[int] = None, reuse_channel: Optional[bool] = None, max_slots: Optional[int] = None, notify_enabled: Optional[bool] = None, name_format: Optional[str] = None, cooldown: Optional[int] = None, mention_role: Optional[discord.Role] = None, log_role: Optional[discord.Role] = None, ignore_role: Optional[discord.Role] = None, reset_roles: bool = False):
        g = self.db.get_guild_config(itx.guild_id)
        msg = self._update_settings_logic(g, is_guild=True, category=category, assignee_role=assignee_role, assignee_qual_role=assignee_qual_role, transcript=transcript, timeout_hours=timeout_hours, auto_close_enabled=auto_close_enabled, auto_close_days=auto_close_days, reuse_channel=reuse_channel, max_slots=max_slots, notify_enabled=notify_enabled, name_format=name_format, cooldown=cooldown, mention_role=mention_role, log_role=log_role, ignore_role=ignore_role, reset_roles=reset_roles)
        self.db.save_profiles()
        await itx.response.defer(ephemeral=True)
        embed = await self.create_admin_dashboard_embed(itx.guild)
        await itx.followup.send(embed=embed, view=AdminDashboardView(self, itx.guild), ephemeral=True)

    @admin_group.command(name="dashboard", description="サーバー設定確認")
    async def admin_dash(self, itx: discord.Interaction):
        embed = await self.create_admin_dashboard_embed(itx.guild)
        await itx.response.send_message(embed=embed, view=AdminDashboardView(self, itx.guild), ephemeral=True)

    @admin_group.command(name="manage", description="指定したチケットの管理メニューを呼び出します")
    async def admin_manage(self, itx: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        target_channel = channel or itx.channel
        gid, cid = str(itx.guild_id), str(target_channel.id)
        if cid not in self.db.timers.get(gid, {}):
            await itx.response.send_message(f"⚠️ {target_channel.mention} はチケットとして登録されていません。", ephemeral=True)
            return
        t_data = self.db.timers[gid][cid]
        embed = await self.create_ticket_dashboard_embed(target_channel, t_data)
        await itx.response.send_message(embed=embed, view=AssigneeMenuView(target_channel, itx.message.id), ephemeral=True)

    @admin_group.command(name="link", description="チケット紐付け")
    async def admin_link(self, itx: discord.Interaction, channel: discord.TextChannel, thread_id: Optional[str] = None, create_thread: bool = False, assignee: Optional[discord.Member] = None, creator: Optional[discord.Member] = None):
        gid, cid = str(itx.guild_id), str(channel.id)
        is_new = False
        if cid not in self.db.timers.get(gid, {}):
            if not assignee:
                await itx.response.send_message("⚠️ assignee指定必須", ephemeral=True)
                return
            c_id = creator.id if creator else assignee.id
            p = self.db.get_user_profile(itx.guild_id, assignee.id)
            embed = discord.Embed(title=f"✅ 登録: {channel.name}", color=discord.Color.green())
            msg = await channel.send(embed=embed, view=TicketControlView())
            self.db.timers[gid][cid] = {"last_message_at": datetime.datetime.now().isoformat(), "enabled": self._get_setting(itx.guild_id, p, "notify_enabled", DEFAULT_NOTIFY_ENABLED), "timeout_hours": self._get_setting(itx.guild_id, p, "timeout_hours", DEFAULT_TIMEOUT_HOURS), "assignee_id": assignee.id, "creator_id": c_id, "active_tickets": [msg.id], "auto_close_enabled": True, "auto_close_days": self._get_setting(itx.guild_id, p, "auto_close_days", DEFAULT_AUTO_CLOSE_DAYS), "mirror_thread_id": None, "last_log_at": None, "tasks": {str(msg.id): []}}
            self.db.save_timers()
            is_new = True
        if thread_id:
            try:
                t = await channel.guild.fetch_channel(int(thread_id))
                self.db.timers[gid][cid]["mirror_thread_id"] = t.id
                self.db.save_timers()
                await itx.response.send_message(f"🔗 {t.mention} 紐付け完了", ephemeral=True)
            except:
                await itx.response.send_message("⚠️ 不明ID", ephemeral=True)
            return
        if create_thread:
            aid = self.db.timers[gid][cid]["assignee_id"]
            p = self.db.get_user_profile(itx.guild_id, aid)
            m_list = [f"<@{aid}>"]
            r_ids = p.get("mention_roles")
            if not r_ids:
                r_ids = self.db.get_guild_config(itx.guild_id).get("mention_roles", [])
            for r in r_ids:
                m_list.append(f"<@&{r}>")
            await self._init_forum_thread(channel, discord.Embed(title="Transcript", description=f"Source: {channel.mention}"), p, m_list)
            await itx.response.send_message(f"🆕 ログ作成完了", ephemeral=True)
            return
        await itx.response.send_message(f"{'✅ 済' if not is_new else '🆕 新規'}", ephemeral=True)

    @admin_group.command(name="recover", description="スキャン復旧")
    async def admin_recover(self, itx: discord.Interaction, category: discord.CategoryChannel, dry_run: bool = False):
        await itx.response.defer(ephemeral=True)
        gid = str(itx.guild_id)
        g = self.db.get_guild_config(itx.guild_id)
        rid = g.get("assignee_role_id")
        recovered = 0
        log = []
        if not rid:
            await itx.followup.send("⚠️ 担当ロール未設定", ephemeral=True)
            return
        for ch in category.text_channels:
            cid = str(ch.id)
            if cid in self.db.timers.get(gid, {}):
                continue
            ta = None
            tc = None
            for target, ow in ch.overwrites.items():
                if isinstance(target, discord.Member) and not target.bot:
                    if any(r.id == rid for r in target.roles):
                        ta = target
                    elif ow.read_messages:
                        tc = target
            if ta:
                recovered += 1
                if not dry_run: 
                    c_id = tc.id if tc else ta.id
                    p = self.db.get_user_profile(itx.guild_id, ta.id)
                    self.db.timers[gid][cid] = {"last_message_at": datetime.datetime.now().isoformat(), "enabled": self._get_setting(itx.guild_id, p, "notify_enabled", DEFAULT_NOTIFY_ENABLED), "timeout_hours": self._get_setting(itx.guild_id, p, "timeout_hours", DEFAULT_TIMEOUT_HOURS), "assignee_id": ta.id, "creator_id": c_id, "active_tickets": [], "auto_close_enabled": True, "auto_close_days": self._get_setting(itx.guild_id, p, "auto_close_days", DEFAULT_AUTO_CLOSE_DAYS), "mirror_thread_id": None, "last_log_at": None, "tasks": {}}
                log.append(f"✅ {ch.name}: {ta.display_name}")
        if not dry_run:
            self.db.save_timers()
        await itx.followup.send(f"🚀 復旧完了 ({recovered}件)\n" + "\n".join(log[:10]), ephemeral=True)

    @admin_group.command(name="assignee", description="【管理者】担当者個別設定")
    async def admin_assignee(self, itx: discord.Interaction, target: discord.Member, category: Optional[discord.CategoryChannel] = None, transcript: Optional[discord.ForumChannel] = None, timeout_hours: Optional[int] = None, auto_close_enabled: Optional[bool] = None, auto_close_days: Optional[int] = None, reuse_channel: Optional[bool] = None, max_slots: Optional[int] = None, cooldown: Optional[int] = None, notify_enabled: Optional[bool] = None, name_format: Optional[str] = None, mention_role: Optional[discord.Role] = None, log_role: Optional[discord.Role] = None, ignore_role: Optional[discord.Role] = None, reset_roles: bool = False):
        p = self.db.get_user_profile(itx.guild_id, target.id)
        msg = self._update_settings_logic(p, is_guild=False, category=category, transcript=transcript, timeout_hours=timeout_hours, auto_close_enabled=auto_close_enabled, auto_close_days=auto_close_days, reuse_channel=reuse_channel, max_slots=max_slots, cooldown=cooldown, notify_enabled=notify_enabled, name_format=name_format, mention_role=mention_role, log_role=log_role, ignore_role=ignore_role, reset_roles=reset_roles)
        self.db.save_profiles()
        await itx.response.defer(ephemeral=True)
        embed = await self.create_assignee_detail_embed(itx.guild, target.id, target.display_name)
        await itx.followup.send(embed=embed, view=AdminAssigneeDetailView(self, itx.guild), ephemeral=True)

    @my_group.command(name="setup", description="個人設定")
    async def my_setup(self, itx: discord.Interaction, transcript: Optional[discord.ForumChannel] = None, timeout_hours: Optional[int] = None, auto_close_enabled: Optional[bool] = None, auto_close_days: Optional[int] = None, reuse_channel: Optional[bool] = None, max_slots: Optional[int] = None, cooldown: Optional[int] = None, notify_enabled: Optional[bool] = None, name_format: Optional[str] = None, mention_role: Optional[discord.Role] = None, log_role: Optional[discord.Role] = None, ignore_role: Optional[discord.Role] = None, reset_roles: bool = False):
        p = self.db.get_user_profile(itx.guild_id, itx.user.id)
        msg = self._update_settings_logic(p, is_guild=False, transcript=transcript, timeout_hours=timeout_hours, auto_close_enabled=auto_close_enabled, auto_close_days=auto_close_days, reuse_channel=reuse_channel, max_slots=max_slots, cooldown=cooldown, notify_enabled=notify_enabled, name_format=name_format, mention_role=mention_role, log_role=log_role, ignore_role=ignore_role, reset_roles=reset_roles)
        self.db.save_profiles()
        await itx.response.defer(ephemeral=True)
        embed = await self.create_my_dashboard_embed(itx.guild, itx.user)
        await itx.followup.send(embed=embed, view=MyDashboardView(), ephemeral=True)

    @my_group.command(name="dashboard", description="個人ダッシュボード")
    async def my_dash(self, itx: discord.Interaction):
        embed = await self.create_my_dashboard_embed(itx.guild, itx.user)
        await itx.response.send_message(embed=embed, view=MyDashboardView(), ephemeral=True)

    @attr_group.command(name="set", description="属性設定")
    async def attr_set(self, itx: discord.Interaction, user: discord.Member, key: str, value: int):
        g = self.db.get_guild_config(itx.guild_id)
        p = self.db.get_user_profile(itx.guild_id, user.id)
        if key not in g["attributes"]:
            g["attributes"][key] = {"order": "desc"}
        p["attributes"][key] = value
        self.db.save_profiles()
        await itx.response.send_message(f"✅ Set [{key}:{value}]", ephemeral=True)
    
    @attr_group.command(name="list", description="属性一覧")
    async def attr_list(self, itx: discord.Interaction):
        g = self.db.get_guild_config(itx.guild_id)
        await itx.response.send_message(f"📋 Attributes: {list(g.get('attributes', {}).keys())}", ephemeral=True)

    @ticket_group.command(name="panel", description="作成パネル")
    async def panel_cmd(self, itx: discord.Interaction, title: str = "依頼受付", description: str = "ボタンを押して作成してください。"):
        embed = discord.Embed(title=title, description=description.replace("\\n", "\n"), color=discord.Color.blue())
        await itx.channel.send(embed=embed, view=TicketPanelView())
        await itx.response.send_message("✅ 設置完了", ephemeral=True)

    @ticket_group.command(name="create", description="手動作成")
    async def create_cmd(self, itx: discord.Interaction, assignee: discord.Member):
        err = self.check_accept_status(itx.guild, assignee, itx.user) 
        if err:
            await itx.response.send_message(err, ephemeral=True)
        else:
            await itx.response.send_modal(ContractModal(assignee))

async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))


