import discord
from discord import app_commands, ui
from discord.ext import commands
import logging
import json
import os
import datetime
from typing import Dict, Any

logger = logging.getLogger("discord_bot.cogs.members")
DATA_FILE = os.path.join("data", "members_settings.json")

class Apply(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.settings = self.load_settings()

    # --- Settings Management ---
    def load_settings(self) -> Dict[str, Any]:
        if not os.path.exists(DATA_FILE):
            return {}
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load members settings: {e}")
            return {}

    def save_settings(self):
        try:
            os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save members settings: {e}")

    def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        if gid not in self.settings:
            self.settings[gid] = {
                "archive_forum_id": None, # 申請内容を保存するフォーラム
                "member_role_id": None    # 承認時に与えるロール
            }
        return self.settings[gid]

    # --- Commands ---
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


# --- View (Entry Button) ---
class ApplyEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="情報を入力する", style=discord.ButtonStyle.success, custom_id="members_open_btn")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplicationModal())

# --- Modal (Questionnaire) ---
class ApplicationModal(ui.Modal, title="クリエイター名簿 登録用アンケート"):
    # 制限: 最大5項目まで
    
    # 1. 名義
    nickname = ui.TextInput(
        label="活動名義（サーバー内呼称）",
        placeholder="例：ファム・ファタル",
        required=True,
        max_length=50
    )
    
    # 2. 連絡先 (SNS / Email)
    contact = ui.TextInput(
        label="連絡先 (SNSアカウント / Email)",
        placeholder="X ID: @..., Email: example@...",
        style=discord.TextStyle.paragraph, # 複数行可
        required=True,
        max_length=300
    )

    # 3. 実績など
    works = ui.TextInput(
        label="過去の実績 / ポートフォリオURL など（あれば）",
        placeholder="YouTube 再生リスト URL, spotify プレイリスト, etc",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=300
    )
    
    # 4. 制作環境
    environment = ui.TextInput(
        label="制作環境 (DAW / 使用機材など)",
        placeholder="Cubase 13, UAD Apollo Twin X...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=300
    )

    # 5. やりたいこと
    ambition = ui.TextInput(
        label="今後やりたいこと / リファレンス / 意気込みなど",
        placeholder="こんな曲をやってみたい、こういうミックスがしたい、等。依頼先の選定にあたり積極的に参考にします。できるだけ書いてもらえるとたすかります。",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Apply")
        if not cog:
            return

        settings = cog.get_guild_settings(interaction.guild_id)
        forum_id = settings.get("archive_forum_id")
        
        if not forum_id:
            await interaction.response.send_message("エラー: 保存先フォーラムが設定されていません。", ephemeral=True)
            return

        forum = interaction.guild.get_channel(forum_id)
        if not forum:
            await interaction.response.send_message("エラー: フォーラムが見つかりません。", ephemeral=True)
            return

        # Build Embed for Archive
        embed = discord.Embed(
            title=f"申請書: {self.nickname.value}",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        
        # Embedに情報を詰める
        embed.add_field(name="👤 活動名義", value=self.nickname.value, inline=False)
        embed.add_field(name="📧 連絡先", value=self.contact.value, inline=False)
        embed.add_field(name="🎵 実績", value=self.works.value or "なし", inline=False)
        embed.add_field(name="💻 制作環境", value=self.environment.value or "未回答", inline=False)
        embed.add_field(name="✨ 自己PR", value=self.ambition.value, inline=False)
        
        embed.set_footer(text=f"Discord User ID: {interaction.user.id}")

        # Create Thread in Forum
        # タイトルフォーマット: YYMMDD_ニックネーム
        date_str = datetime.datetime.now().strftime("%y%m%d")
        safe_name = self.nickname.value.replace(" ", "_")
        thread_name = f"{date_str}_{safe_name}"
        
        # スレッドを作成し、承認ボタン付きのメッセージを送信
        thread_with_message = await forum.create_thread(
            name=thread_name,
            content=f"{interaction.user.mention} からの入力内容です。",
            embed=embed,
            view=ApproveView(interaction.user.id)
        )
        
        await interaction.response.send_message("入力を受付けました。管理者の承認をお待ちください。", ephemeral=True)


# --- View (Admin Approval) ---
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
            await interaction.response.send_message("設定エラー: 付与するロールが設定されていません。", ephemeral=True)
            return

        role = interaction.guild.get_role(role_id)
        # キャッシュにいない場合のために fetch_member を使用（API通信発生するが確実）
        try:
            target_member = await interaction.guild.fetch_member(self.target_user_id)
        except discord.NotFound:
            await interaction.response.send_message("エラー: ユーザーがサーバーに見つかりません（退出した可能性があります）。", ephemeral=True)
            return

        try:
            await target_member.add_roles(role, reason="Application Approved")
            
            # ニックネームも変更案（権限が必要なので失敗する可能性あり。今回はロール付与のみを優先）
            # await target_member.edit(nick=nickname_from_embed) 
            
            # Disable button and update message
            button.label = "承認済み"
            button.style = discord.ButtonStyle.secondary
            button.disabled = True
            await interaction.response.edit_message(view=self)
            
            await interaction.followup.send(f"{target_member.mention} を承認しました。\nロール `{role.name}` を付与しました。", ephemeral=True)
            
        except discord.Forbidden:
            await interaction.response.send_message("権限エラー: ロールを付与できませんでした。Botの権限がロールより上にあるか確認してください。", ephemeral=True)

async def setup(bot: commands.Bot):
    cog = Apply(bot)
    await bot.add_cog(cog)
    bot.add_view(ApplyEntryView())
