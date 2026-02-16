import discord
from discord import app_commands
from discord.ext import commands
import logging
import os
from typing import Dict, Any, Optional
from utils.storage import JsonHandler

logger = logging.getLogger("discord_bot.cogs.roles")
DATA_FILE = os.path.join("data", "roles.json")

class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = JsonHandler(DATA_FILE)
        self.config = self.db.load()

    def save_config(self):
        self.db.save(self.config)

    def get_guild_config(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        if gid not in self.config:
            self.config[gid] = {
                "qualified_role_id": None,
                "info_channel_id": None
            }
        return self.config[gid]

    def _get_qualified_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        conf = self.get_guild_config(guild.id)
        rid = conf.get("qualified_role_id")
        return guild.get_role(rid) if rid else None

    def _build_candidate_map(self, guild: discord.Guild) -> Dict[int, discord.Role]:
        name_to_role = {r.name: r for r in guild.roles}
        cand_to_real = {}
        prefix = "__"
        for role in guild.roles:
            if role.name.startswith(prefix):
                real_name = role.name[len(prefix):]
                real_role = name_to_role.get(real_name)
                if real_role:
                    cand_to_real[role.id] = real_role
        return cand_to_real

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles: return
        guild = after.guild
        qualified_role = self._get_qualified_role(guild)
        if not qualified_role: return

        cand_map = self._build_candidate_map(guild)
        if not cand_map: return

        before_roles = set(before.roles)
        after_roles = set(after.roles)
        
        has_qualified_before = qualified_role in before_roles
        has_qualified_now = qualified_role in after_roles

        added_roles = after_roles - before_roles
        removed_roles = before_roles - after_roles
        
        roles_to_add = set()
        roles_to_remove = set()

        if not has_qualified_before and has_qualified_now:
            for role in after_roles:
                if role.id in cand_map:
                    real_role = cand_map[role.id]
                    if real_role not in after_roles: roles_to_add.add(real_role)

        elif has_qualified_before and not has_qualified_now:
           for role in after_roles:
               if role.id in cand_map:
                   real_role = cand_map[role.id]
                   if real_role in after_roles: roles_to_remove.add(real_role)

        if has_qualified_now:
            for role in added_roles:
                if role.id in cand_map:
                    real_role = cand_map[role.id]
                    if real_role not in after_roles: roles_to_add.add(real_role)

        for role in removed_roles:
            if role.id in cand_map:
                real_role = cand_map[role.id]
                if real_role in after_roles: roles_to_remove.add(real_role)

        if roles_to_add: await after.add_roles(*roles_to_add, reason="Role Sync")
        if roles_to_remove: await after.remove_roles(*roles_to_remove, reason="Role Sync")

    role_group = app_commands.Group(name="roles", description="ロール管理コマンド")

    @role_group.command(name="setup", description="Qualifiedロールと案内チャンネルを設定します")
    async def setup(self, interaction: discord.Interaction, role: discord.Role, info_channel: discord.TextChannel):
        conf = self.get_guild_config(interaction.guild_id)
        conf["qualified_role_id"] = role.id
        conf["info_channel_id"] = info_channel.id
        self.save_config()
        await interaction.response.send_message(f"設定を保存しました。\nロール: {role.mention}\n案内チャンネル: {info_channel.mention}", ephemeral=True)

    @role_group.command(name="panel", description="資格取得ボタンのパネルを設置します")
    async def panel(self, interaction: discord.Interaction):
        conf = self.get_guild_config(interaction.guild_id)
        rid = conf.get("qualified_role_id")
        cid = conf.get("info_channel_id")
        if not rid or not cid:
            await interaction.response.send_message("エラー: 先に `/roles setup` を実行してください。", ephemeral=True); return

        role = interaction.guild.get_role(rid)
        channel = interaction.guild.get_channel(cid)
        if not role or not channel:
            await interaction.response.send_message("エラー: 設定されたロールまたはチャンネルが見つかりません。", ephemeral=True); return

        description = f"{channel.mention} の内容を読了した方は、以下のボタンを押して {role.mention} ロールを取得してください。"
        embed = discord.Embed(title="確認", description=description, color=discord.Color.blue())
        view = QualifyView()
        
        await interaction.response.send_message("パネルを設置しました。", ephemeral=True)
        await interaction.channel.send(embed=embed, view=view)

class QualifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="読了しました", style=discord.ButtonStyle.primary, custom_id="qualify_btn")
    async def qualify(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("Roles")
        if not cog: return

        qualified_role = cog._get_qualified_role(interaction.guild)
        if not qualified_role:
            await interaction.followup.send("設定エラー: Role未設定", ephemeral=True); return

        if qualified_role in interaction.user.roles:
            await interaction.followup.send("既にロールを持っています。", ephemeral=True); return

        try:
            await interaction.user.add_roles(qualified_role, reason="Qualify button clicked")
            await interaction.followup.send("ロールを付与しました。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("エラー: Botに権限がありません。", ephemeral=True)

async def setup(bot: commands.Bot):
    cog = Roles(bot)
    await bot.add_cog(cog)
    bot.add_view(QualifyView())
