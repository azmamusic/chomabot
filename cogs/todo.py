import discord
from discord import app_commands, ui
from discord.ext import commands
import json
import os
import datetime
import uuid
from typing import Optional, Dict, Any, List

DATA_FILE = os.path.join("data", "todo_settings.json")

class ToDo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = self.load_data()

    def load_data(self) -> Dict[str, Any]:
        if not os.path.exists(DATA_FILE): return {}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: return {}

    def save_data(self):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(self.data, f, indent=4)

    def get_guild_config(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        if gid not in self.data:
            self.data[gid] = {}
        
        defaults = {
            "role_ids": [],
            "tasks": {},
            "profiles": {}
        }
        
        for k, v in defaults.items():
            if k not in self.data[gid]:
                self.data[gid][k] = v

        return self.data[gid]

    def get_user_profile(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        g_conf = self.get_guild_config(guild_id)
        uid = str(user_id)
        
        if uid not in g_conf["profiles"]:
            g_conf["profiles"][uid] = {}
            
        p = g_conf["profiles"][uid]
        
        # デフォルト値の定義
        if "default_channel_id" not in p: p["default_channel_id"] = None
        # ★追加: Noneならサーバー設定を使用、リストなら個人設定を使用
        if "mention_role_ids" not in p: p["mention_role_ids"] = None 
        
        return p

    # --- Task Management Helpers ---
    def save_task(self, guild_id: int, message_id: int, title: str, description: str, author_id: int):
        conf = self.get_guild_config(guild_id)
        conf["tasks"][str(message_id)] = {
            "title": title,
            "description": description,
            "status": "open",
            "author_id": author_id,
            "created_at": datetime.datetime.now().isoformat()
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

    # ====================================================
    # Commands
    # ====================================================
    todo_group = app_commands.Group(name="todo", description="ToDo管理機能")
    setup_group = app_commands.Group(name="setup", description="ToDo機能の設定(管理者用)", parent=todo_group)
    my_group = app_commands.Group(name="my", description="ToDo機能の個人設定", parent=todo_group)

    # --- Setup Commands (Admin) ---

    @setup_group.command(name="add", description="デフォルトの通知対象ロールを追加")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def setup_add(self, itx: discord.Interaction, role: discord.Role):
        conf = self.get_guild_config(itx.guild_id)
        current_roles = conf.get("role_ids", [])
        if role.id not in current_roles:
            current_roles.append(role.id)
            conf["role_ids"] = current_roles
            self.save_data()
            await itx.response.send_message(f"✅ サーバー通知先に {role.mention} を追加しました。", ephemeral=True)
        else:
            await itx.response.send_message(f"⚠️ {role.mention} は既に追加されています。", ephemeral=True)

    @setup_group.command(name="remove", description="デフォルトの通知対象ロールを削除")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def setup_remove(self, itx: discord.Interaction, role: discord.Role):
        conf = self.get_guild_config(itx.guild_id)
        current_roles = conf.get("role_ids", [])
        if role.id in current_roles:
            current_roles.remove(role.id)
            conf["role_ids"] = current_roles
            self.save_data()
            await itx.response.send_message(f"🗑️ サーバー通知先から {role.mention} を削除しました。", ephemeral=True)
        else:
            await itx.response.send_message(f"⚠️ {role.mention} は設定されていません。", ephemeral=True)

    @setup_group.command(name="list", description="現在設定されているデフォルト通知先を表示")
    async def setup_list(self, itx: discord.Interaction):
        conf = self.get_guild_config(itx.guild_id)
        role_ids = conf.get("role_ids", [])
        if not role_ids:
            await itx.response.send_message("通知先ロールは設定されていません。", ephemeral=True); return
        mentions = []
        for rid in role_ids:
            role = itx.guild.get_role(rid)
            mentions.append(role.mention if role else f"(削除済: {rid})")
        await itx.response.send_message(f"📋 **サーバーデフォルト通知先:**\n" + "\n".join(mentions), ephemeral=True)

    # --- My Settings Commands (User) ---

    @my_group.command(name="setup", description="自分のタスクの投稿先や通知設定を変更")
    @app_commands.describe(
        channel="デフォルトの投稿先 (指定しない場合は変更なし)",
        add="作成時にメンションするロールを追加",
        remove="作成時にメンションするロールを削除",
        reset="メンション設定をサーバーデフォルトに戻す (Trueで実行)"
    )
    async def my_setup(self, itx: discord.Interaction, 
                       channel: Optional[discord.TextChannel] = None,
                       add: Optional[discord.Role] = None,
                       remove: Optional[discord.Role] = None,
                       reset: Optional[bool] = False):
        
        p = self.get_user_profile(itx.guild_id, itx.user.id)
        msg_parts = []
        
        # 1. チャンネル設定
        if channel:
            p["default_channel_id"] = channel.id
            msg_parts.append(f"• 投稿先: {channel.mention}")
        elif channel is None:
            # 引数なしの場合、明示的に解除操作でなければ何もしない（他の設定だけ変えたい場合のため）
            pass

        # 2. メンション設定
        # リセット処理
        if reset:
            p["mention_role_ids"] = None
            msg_parts.append("• メンション: [サーバーデフォルトに戻しました]")
        
        # 追加/削除処理
        updated_mentions = False
        
        if add or remove:
            # 現在値が None (デフォルト) なら、空リストで初期化して個人設定モードにする
            if p["mention_role_ids"] is None:
                p["mention_role_ids"] = []
                # ここで何も追加しないと「誰もメンションしない」設定になる。
                # 初回はサーバーデフォルトを引き継ぎたい場合は下記のようにするが、
                # 今回は「個人設定＝完全上書き」とする（わかりやすさ重視）

            current_list = p["mention_role_ids"]

            if add:
                if add.id not in current_list:
                    current_list.append(add.id)
                    msg_parts.append(f"• メンション追加: {add.mention}")
                    updated_mentions = True
            
            if remove:
                if remove.id in current_list:
                    current_list.remove(remove.id)
                    msg_parts.append(f"• メンション削除: {remove.mention}")
                    updated_mentions = True
            
            p["mention_role_ids"] = current_list

        self.save_data()
        
        if not msg_parts:
            await itx.response.send_message("⚠️ 変更項目を指定してください。\n(投稿先解除は `/todo my setup` でチャンネルを選ばずに実行するのではなく、専用コマンドにするか、現状維持とみなしています)", ephemeral=True)
        else:
            await itx.response.send_message(f"⚙️ **個人設定を更新しました**\n" + "\n".join(msg_parts), ephemeral=True)

    @my_group.command(name="status", description="自分の設定状況を確認")
    async def my_status(self, itx: discord.Interaction):
        p = self.get_user_profile(itx.guild_id, itx.user.id)
        g_conf = self.get_guild_config(itx.guild_id)
        
        embed = discord.Embed(title=f"⚙️ ToDo個人設定: {itx.user.display_name}", color=discord.Color.blue())

        # 投稿先
        cid = p.get("default_channel_id")
        if cid:
            ch = itx.guild.get_channel(cid)
            status_val = ch.mention if ch else f"(不明: {cid})"
            status_desc = "固定 (設定済み)"
        else:
            status_val = "コマンド実行場所"
            status_desc = "デフォルト (未設定)"
        embed.add_field(name="📮 デフォルト投稿先", value=f"{status_val}\n└ {status_desc}", inline=True)

        # メンション
        u_mentions = p.get("mention_role_ids")
        if u_mentions is not None:
            # 個人設定あり
            if not u_mentions:
                m_str = "🔕 なし (通知しない)"
            else:
                m_list = []
                for rid in u_mentions:
                    r = itx.guild.get_role(rid)
                    m_list.append(r.mention if r else "(削除済)")
                m_str = ", ".join(m_list)
            m_desc = "個人設定 (サーバー設定を無視)"
        else:
            # サーバー設定を使用
            g_mentions = g_conf.get("role_ids", [])
            if not g_mentions:
                m_str = "🔕 なし"
            else:
                m_list = []
                for rid in g_mentions:
                    r = itx.guild.get_role(rid)
                    m_list.append(r.mention if r else "(削除済)")
                m_str = ", ".join(m_list)
            m_desc = "サーバーデフォルト"
        
        embed.add_field(name="📢 メンション対象", value=f"{m_str}\n└ {m_desc}", inline=False)
        
        await itx.response.send_message(embed=embed, ephemeral=True)

    # --- Main ToDo Commands ---

    @todo_group.command(name="new", description="新しいタスクを作成 (モーダルが開きます)")
    @app_commands.describe(ref_channel="関連するチャンネル (任意)")
    async def new_todo(self, itx: discord.Interaction, ref_channel: Optional[discord.TextChannel] = None):
        await itx.response.send_modal(ToDoCreateModal(ref_channel))


# --- UI Classes ---

class ToDoCreateModal(ui.Modal, title="新規タスク作成"):
    def __init__(self, ref_channel: Optional[discord.TextChannel]):
        super().__init__()
        self.ref_channel = ref_channel
        self.task_title = ui.TextInput(label="タスク件名", placeholder="未入力で自動IDを割り当て", max_length=100)
        self.task_desc = ui.TextInput(label="詳細内容", placeholder="詳細やコードなどを入力...", style=discord.TextStyle.paragraph, required=False, max_length=2000)
        self.add_item(self.task_title); self.add_item(self.task_desc)

    async def on_submit(self, itx: discord.Interaction):
        cog = itx.client.get_cog("ToDo")
        if not cog: return

        conf = cog.get_guild_config(itx.guild_id)
        profile = cog.get_user_profile(itx.guild_id, itx.user.id)
        
        # 1. 投稿先の決定
        target_channel = itx.channel
        default_cid = profile.get("default_channel_id")
        if default_cid:
            found_ch = itx.guild.get_channel(default_cid)
            if found_ch: target_channel = found_ch

        # 2. メンションの決定
        # 個人設定(None以外)があればそれを使う。なければサーバー設定を使う
        u_mentions = profile.get("mention_role_ids")
        target_role_ids = u_mentions if u_mentions is not None else conf.get("role_ids", [])
        
        mentions_str = " ".join([f"<@&{rid}>" for rid in target_role_ids])

        # 3. タイトル生成
        final_title = self.task_title.value.strip()
        if not final_title:
            unique_id = str(uuid.uuid4())[:8]
            final_title = f"Task-{unique_id}"

        # 4. Embed作成
        embed = discord.Embed(
            title=f"📝 {final_title}",
            description=self.task_desc.value,
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now()
        )
        embed.set_author(name=itx.user.display_name, icon_url=itx.user.display_avatar.url)
        if self.ref_channel:
            embed.add_field(name="関連チャンネル", value=self.ref_channel.mention, inline=False)
        embed.set_footer(text="Status: Open")

        view = ToDoView()
        
        try:
            msg = await target_channel.send(content=mentions_str, embed=embed, view=view)
            cog.save_task(itx.guild_id, msg.id, final_title, self.task_desc.value, itx.user.id)
            
            if target_channel.id == itx.channel.id:
                await itx.response.send_message(f"✅ タスクを作成しました。", ephemeral=True)
            else:
                await itx.response.send_message(f"✅ {target_channel.mention} にタスクを作成しました。\n{msg.jump_url}", ephemeral=True)
                
        except discord.Forbidden:
             await itx.response.send_message(f"❌ エラー: 設定されたチャンネル {target_channel.mention} に書き込む権限がありません。", ephemeral=True)
        except Exception as e:
             await itx.response.send_message(f"❌ エラーが発生しました: {e}", ephemeral=True)

class ToDoView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Text", style=discord.ButtonStyle.secondary, custom_id="todo_text", emoji="📄")
    async def show_text(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("ToDo")
        task_data = None
        if cog:
            task_data = cog.get_task(itx.guild_id, itx.message.id)
        
        content = ""
        if task_data:
            content = task_data.get("description", "")
        elif itx.message.embeds:
            content = itx.message.embeds[0].description
        
        if content:
            await itx.response.send_message(content, ephemeral=True)
        else:
            await itx.response.send_message("⚠️ 内容を取得できませんでした。", ephemeral=True)

    @discord.ui.button(label="Resolve", style=discord.ButtonStyle.success, custom_id="todo_complete")
    async def complete(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("ToDo")
        if not cog: return

        task_data = cog.get_task(itx.guild_id, itx.message.id)
        is_completed = False
        
        if task_data:
            if task_data.get("status") == "completed": is_completed = True
        else:
            if itx.message.embeds and "Resolved" in itx.message.embeds[0].title: is_completed = True

        if is_completed:
            await itx.response.send_message("既に完了しています。", ephemeral=True)
            return
            
        cog.update_task_status(itx.guild_id, itx.message.id, "completed")

        embed = itx.message.embeds[0]
        title_text = task_data["title"] if task_data else embed.title.replace("📝 ", "").replace("✅ Resolved: ", "").strip()
        
        embed.title = f"✅ Resolved: {title_text}"
        embed.color = discord.Color.green()
        embed.set_footer(text=f"Resolved by: {itx.user.display_name}")
        
        self.remove_item(button)
        
        await itx.message.edit(embed=embed, view=self)
        await itx.response.send_message(f"👍 **Resolved!** ({itx.user.display_name} が対応しました)", ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="todo_delete")
    async def delete(self, itx: discord.Interaction, button: discord.ui.Button):
        cog = itx.client.get_cog("ToDo")
        if cog: cog.delete_task_data(itx.guild_id, itx.message.id)
        
        await itx.message.delete()
        await itx.response.send_message("🗑️ タスクを削除しました。", ephemeral=True)

class DeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Delete", style=discord.ButtonStyle.danger, custom_id="todo_delete_only")
    
    async def callback(self, itx: discord.Interaction):
        cog = itx.client.get_cog("ToDo")
        if cog: cog.delete_task_data(itx.guild_id, itx.message.id)

        await itx.message.delete()
        await itx.response.send_message("🗑️ タスクを削除しました。", ephemeral=True)

async def setup(bot: commands.Bot):
    bot.add_view(ToDoView())
    await bot.add_cog(ToDo(bot))
