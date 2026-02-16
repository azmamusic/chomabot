import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
from datetime import datetime
from typing import Union, Optional

logger = logging.getLogger("discord_bot.cogs.move")

class Move(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _parse_date(self, date_str: str):
        if not date_str: return None
        formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"]
        for fmt in formats:
            try: return datetime.strptime(date_str, fmt)
            except ValueError: continue
        return None

    async def _resolve_channel(self, guild, input_val: Union[discord.abc.GuildChannel, discord.Thread, str, None]):
        """
        ID指定のみを受け付ける厳格な解決ロジック。
        名前検索は行わない。
        """
        if input_val is None: return None
        
        # 既にオブジェクトになっている場合 (青いチップ)
        if isinstance(input_val, (discord.abc.GuildChannel, discord.Thread)):
            return input_val
        
        # 文字列の正規化 (クォート除去)
        text = str(input_val).strip().replace('"', '').replace("'", "")
        
        # ID解析
        target_id = None
        if text.isdigit():
            target_id = int(text)
        elif text.startswith("<#") and text.endswith(">"):
            try: target_id = int(text[2:-1])
            except: pass
            
        if target_id:
            return guild.get_channel_or_thread(target_id)
            
        return None

    async def _get_webhook(self, channel):
        if isinstance(channel, discord.Thread): channel = channel.parent
        if not isinstance(channel, discord.TextChannel): return None
        try:
            webhooks = await channel.webhooks()
            webhook = discord.utils.get(webhooks, name="MoveBotWebhook")
            if webhook is None: webhook = await channel.create_webhook(name="MoveBotWebhook")
            return webhook
        except Exception as e: logger.warning(f"Webhook error: {e}"); return None

    async def _copy_messages(self, source: discord.abc.Messageable, target, limit: int, header: str = None, after: datetime = None, before: datetime = None):
        messages = []
        try:
            async for msg in source.history(limit=limit, oldest_first=True, after=after, before=before): messages.append(msg)
        except Exception as e: logger.error(f"History fetch error: {e}"); return 0
        if not messages: return 0
        
        webhook = await self._get_webhook(target)
        target_thread = target if isinstance(target, discord.Thread) else discord.utils.MISSING

        if header:
            separator = discord.Embed(title=f"📂 {header}", description="─────────────────────────────", color=discord.Color.light_grey())
            if after or before: separator.set_footer(text=f"Period: {after or 'Start'} ～ {before or 'Now'}")
            if webhook: await webhook.send(username="System", avatar_url=self.bot.user.display_avatar.url, embed=separator, thread=target_thread)
            elif hasattr(target, "send"): await target.send(embed=separator)

        count = 0
        for msg in messages:
            if msg.content == "" and not msg.embeds and not msg.attachments: continue
            content = msg.content; files = []
            for attachment in msg.attachments:
                try: files.append(await attachment.to_file())
                except: content += f"\n[添付ファイル転送エラー: {attachment.url}]"
            try:
                if webhook: await webhook.send(content=content, username=msg.author.display_name, avatar_url=msg.author.display_avatar.url, embeds=msg.embeds, files=files, thread=target_thread, wait=True)
                elif hasattr(target, "send"): prefix = f"**{msg.author.display_name}**: "; await target.send(content=prefix + content, embeds=msg.embeds, files=files)
                count += 1; await asyncio.sleep(0.7)
            except Exception as e: logger.error(f"Copy error at {count}: {e}"); continue
        return count

    async def _get_forum_threads(self, forum: discord.ForumChannel):
        threads = []
        threads.extend(forum.threads)
        try:
            async for t in forum.archived_threads(limit=None):
                if t.id not in [x.id for x in threads]: threads.append(t)
        except: pass
        try:
            async for t in forum.archived_threads(limit=None, private=True):
                if t.id not in [x.id for x in threads]: threads.append(t)
        except: pass
        threads.sort(key=lambda t: t.created_at or datetime.now().astimezone())
        return threads

    # オートコンプリート (ID入力を補助するために残す)
    async def channel_autocomplete(self, itx: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = []
        threads = itx.guild.threads
        channels = itx.guild.channels
        search_text = current.lower().lstrip('#').replace('"', '')

        # スレッド優先
        for t in threads:
            if search_text in t.name.lower():
                choices.append(app_commands.Choice(name=f"Thread: {t.name}", value=str(t.id)))
            if len(choices) >= 15: break
            
        for c in channels:
            if search_text in c.name.lower():
                choices.append(app_commands.Choice(name=f"Channel: {c.name}", value=str(c.id)))
            if len(choices) >= 25: break
        return choices

    @app_commands.command(name="move", description="構造を考慮してメッセージを移動・アーカイブします")
    @app_commands.describe(target="移動先ID (候補から選択またはID入力)", source="移動元ID (候補から選択またはID入力)", limit="1チャンネルあたりの件数", since="開始(YYYY-MM-DD)", until="終了(YYYY-MM-DD)")
    @app_commands.checks.has_permissions(manage_messages=True, manage_channels=True)
    @app_commands.autocomplete(target=channel_autocomplete, source=channel_autocomplete)
    async def move(self, itx: discord.Interaction, target: str, source: str = None, limit: int = 100, since: str = None, until: str = None):
        await itx.response.defer(ephemeral=True)

        # 1. Source解決
        source_input = source if source else itx.channel
        real_source = await self._resolve_channel(itx.guild, source_input)

        if not real_source:
            # IDじゃなかったら弾く
            await itx.followup.send(f"⚠️ 移動元が見つかりません。**ID** または **候補リスト** から指定してください。\n入力値: `{source}`", ephemeral=True); return

        # 2. Target解決
        real_target = await self._resolve_channel(itx.guild, target)

        if not real_target:
            await itx.followup.send(f"⚠️ 移動先が見つかりません。**ID** または **候補リスト** から指定してください。\n入力値: `{target}`", ephemeral=True); return

        if real_source.id == real_target.id:
            await itx.followup.send(f"⚠️ エラー: 移動元と移動先が同じIDです ({real_source.id})", ephemeral=True); return

        d_after = self._parse_date(since); d_before = self._parse_date(until)
        if (since and not d_after) or (until and not d_before):
            await itx.followup.send("⚠️ 日付形式エラー。`YYYY-MM-DD` 等で指定してください。", ephemeral=True); return

        total_moved = 0; report = []
        s, t = real_source, real_target

        # Logic
        if isinstance(s, discord.CategoryChannel):
            if isinstance(t, discord.CategoryChannel): await itx.followup.send("⚠️ カテゴリ間の移動は手動で行ってください。", ephemeral=True); return
            elif isinstance(t, discord.TextChannel):
                for ch in s.text_channels:
                    c = await self._copy_messages(ch, t, limit, header=f"Source Channel: #{ch.name}", after=d_after, before=d_before)
                    if c > 0: total_moved += c; report.append(f"📄 #{ch.name} -> Flattened ({c})")
            elif isinstance(t, discord.ForumChannel):
                for ch in s.text_channels:
                    new_thread_w_msg = await t.create_thread(name=ch.name, content=f"📦 Moved from #{ch.name}")
                    c = await self._copy_messages(ch, new_thread_w_msg.thread, limit, after=d_after, before=d_before)
                    if c > 0: total_moved += c; report.append(f"🧵 #{ch.name} -> New Thread ({c})")
                    else: await new_thread_w_msg.thread.delete()

        elif isinstance(s, discord.ForumChannel):
            threads = await self._get_forum_threads(s)
            if isinstance(t, discord.CategoryChannel):
                for th in threads:
                    new_ch = await itx.guild.create_text_channel(name=th.name, category=t)
                    c = await self._copy_messages(th, new_ch, limit, after=d_after, before=d_before)
                    if c > 0: total_moved += c; report.append(f"📺 {th.name} -> New Channel ({c})")
                    else: await new_ch.delete()
            elif isinstance(t, discord.TextChannel):
                for th in threads:
                    c = await self._copy_messages(th, t, limit, header=f"Source Thread: {th.name}", after=d_after, before=d_before)
                    if c > 0: total_moved += c; report.append(f"📄 {th.name} -> Flattened ({c})")

        elif isinstance(s, discord.TextChannel):
            if isinstance(t, discord.CategoryChannel):
                new_ch = await itx.guild.create_text_channel(name=s.name, category=t)
                c = await self._copy_messages(s, new_ch, limit, after=d_after, before=d_before)
                if s.threads:
                    for th in s.threads: c += await self._copy_messages(th, new_ch, limit, header=f"Thread: {th.name}", after=d_after, before=d_before)
                if c > 0: total_moved += c; report.append(f"📺 #{s.name} -> New Channel ({c})")
                else: await new_ch.delete()
            elif isinstance(t, discord.ForumChannel):
                new_thread_w_msg = await t.create_thread(name=s.name, content=f"📦 Moved from #{s.name}")
                c = await self._copy_messages(s, new_thread_w_msg.thread, limit, after=d_after, before=d_before)
                if c > 0: total_moved += c; report.append(f"🧵 #{s.name} -> New Thread ({c})")
                else: await new_thread_w_msg.thread.delete()
            elif isinstance(t, discord.TextChannel):
                c = await self._copy_messages(s, t, limit, header=f"Moved: #{s.name}", after=d_after, before=d_before)
                for th in s.threads: c += await self._copy_messages(th, t, limit, header=f"Thread: {th.name}", after=d_after, before=d_before)
                total_moved += c; report.append(f"➡️ #{s.name} -> Merged ({c})")

        elif isinstance(s, discord.Thread):
            if isinstance(t, discord.CategoryChannel): await itx.followup.send("⚠️ スレッドを直接カテゴリに移動する処理は適用外です。", ephemeral=True); return
            elif isinstance(t, discord.TextChannel):
                c = await self._copy_messages(s, t, limit, header=f"Moved Thread: {s.name}", after=d_after, before=d_before)
                total_moved += c; report.append(f"➡️ {s.name} -> Merged ({c})")
            elif isinstance(t, discord.ForumChannel):
                new_thread_w_msg = await t.create_thread(name=s.name, content=f"📦 Moved from Thread: {s.name}")
                c = await self._copy_messages(s, new_thread_w_msg.thread, limit, after=d_after, before=d_before)
                if c > 0: total_moved += c; report.append(f"🧵 {s.name} -> New Post ({c})")
                else: await new_thread_w_msg.thread.delete()

        else: await itx.followup.send("⚠️ 未対応の組み合わせです。", ephemeral=True); return

        summary = "\n".join(report[:15]); 
        if len(report) > 15: summary += f"\n...他 {len(report)-15} 件"
        if total_moved == 0: await itx.followup.send(f"⚠️ 対象メッセージなし (期間: {since or 'All'} ～ {until or 'Now'})", ephemeral=True)
        else: await itx.followup.send(f"✅ **移動完了** ({total_moved}件)\n{summary}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Move(bot))
