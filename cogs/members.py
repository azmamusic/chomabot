import discord
from discord import app_commands, ui
from discord.ext import commands
import logging
import os
import datetime
from typing import Dict, Any
from utils.storage import JsonHandler

logger = logging.getLogger("discord_bot.cogs.members")
DATA_FILE = os.path.join("data", "members_settings.json")

class Apply(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = JsonHandler(DATA_FILE)
        self.settings = self.db.load()

    def save_settings(self):
        self.db.save(self.settings)

    def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        if gid not in self.settings:
            self.settings[gid] = {
                "archive_forum_id": None,
                "member_role_id": None
            }
        return self.settings[gid]

    members_group = app_commands.Group(name="members", description="クリエイター名簿登録用")

    @members_group.command(name="setup", description="入力内容の保存先と、承認によって付与するロールを設定します")
    @app_commands.describe(forum="保存先フォーラムチャンネル", role="承認時に付与するロール")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def setup(self, interaction: discord.Interaction, forum: discord.ForumChannel, role: discord.Role):
        settings = self.get_guild_settings(interaction.guild_id)
        settings["archive_forum_id"] = forum.id
        settings["member_role_id"] = role.id
        self.save_settings()
        
        await interaction.response.send_message(
            f"設定完了しました。\n保存先: {forum.mention}\n付与ロール: {role.mention}", 
            ephemeral=True
        )

    @members_group.command(name="panel", description="申請ボタン（パネル）を設置します")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def panel(self, interaction: discord.Interaction):
        settings = self.get_guild_settings(interaction.guild_id)
        role_id = settings.get("member_role_id")
        
        role = interaction.guild.get_role(role_id) if role_id else None
        
        role_mention = role.mention if role else "メンバーロール"
        description = f"ボタンを押して入力フォームを起動し、必要な情報を入力してください。\n内容の確認・承認が行われると {role_mention} ロールが付与され依頼のやり取りが可能になります。"

        embed = discord.Embed(
            title="クリエイター名簿 登録用アンケート",
            description=description,
            color=discord.Color.gold()
        )
        
        await interaction.channel.send(embed=embed, view=ApplyEntryView())
        await interaction.response.send_message("パネルを設置しました。", ephemeral=True)


class ApplyEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="情報を入力する", style=discord.ButtonStyle.success, custom_id="members_open_btn")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplicationModal())

class ApplicationModal(ui.Modal, title="クリエイター名簿 登録用アンケート"):
    nickname = ui.TextInput(label="活動名義（サーバー内呼称）", placeholder="例：ファム・ファタル", required=True, max_length=50)
    contact = ui.TextInput(label="連絡先 (SNSアカウント / Email)", placeholder="X ID: @..., Email: example@...", style=discord.TextStyle.paragraph, required=True, max_length=300)
    works = ui.TextInput(label="過去の実績 / ポートフォリオURL など（あれば）", placeholder="YouTube, Spotify URL...", style=discord.TextStyle.paragraph, required=False, max_length=300)
    environment = ui.TextInput(label="制作環境 (DAW / 使用機材など)", placeholder="Cubase 13, UAD Apollo...", style=discord.TextStyle.paragraph, required=False, max_length=300)
    ambition = ui.TextInput(label="今後やりたいこと / リファレンス / 意気込みなど", placeholder="自由記述", style=discord.TextStyle.paragraph, required=True, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Apply")
        if not cog: return

        settings = cog.get_guild_settings(interaction.guild_id)
        forum_id = settings.get("archive_forum_id")
        
        if not forum_id:
            await interaction.response.send_message("エラー: 保存先フォーラムが設定されていません。", ephemeral=True); return

        forum = interaction.guild.get_channel(forum_id)
        if not forum:
            await interaction.response.send_message("エラー: フォーラムが見つかりません。", ephemeral=True); return

        embed = discord.Embed(title=f"申請書: {self.nickname.value}", color=discord.Color.green(), timestamp=datetime.datetime.now())
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="👤 活動名義", value=self.nickname.value, inline=False)
        embed.add_field(name="📧 連絡先", value=self.contact.value, inline=False)
        embed.add_field(name="🎵 実績", value=self.works.value or "なし", inline=False)
        embed.add_field(name="💻 制作環境", value=self.environment.value or "未回答", inline=False)
        embed.add_field(name="✨ 自己PR", value=self.ambition.value, inline=False)
        embed.set_footer(text=f"Discord User ID: {interaction.user.id}")

        date_str = datetime.datetime.now().strftime("%y%m%d")
        safe_name = self.nickname.value.replace(" ", "_")
        thread_name = f"{date_str}_{safe_name}"
        
        await forum.create_thread(name=thread_name, content=f"{interaction.user.mention} からの入力内容です。", embed=embed, view=ApproveView(interaction.user.id))
        await interaction.response.send_message("入力を受付けました。管理者の承認をお待ちください。", ephemeral=True)

class ApproveView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id

    @discord.ui.button(label="承認してロール付与", style=discord.ButtonStyle.primary, custom_id="members_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Apply")
        settings = cog.get_guild_settings(interaction.guild_id)
        role_id = settings.get("member_role_id")
        
        if not role_id:
            await interaction.response.send_message("設定エラー: 付与するロールが設定されていません。", ephemeral=True); return

        role = interaction.guild.get_role(role_id)
        try:
            target_member = await interaction.guild.fetch_member(self.target_user_id)
        except discord.NotFound:
            await interaction.response.send_message("エラー: ユーザーがサーバーに見つかりません。", ephemeral=True); return

        try:
            await target_member.add_roles(role, reason="Application Approved")
            button.label = "承認済み"; button.style = discord.ButtonStyle.secondary; button.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"{target_member.mention} を承認しました。", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("権限エラー: ロールを付与できませんでした。", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Apply(bot))
    bot.add_view(ApplyEntryView())
