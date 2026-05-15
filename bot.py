import asyncio
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp


load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
CONFIG_PATH = Path("bot_config.json")

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN. Put it in a .env file or environment variable.")


intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)
commands_synced = False
views_registered = False


YTDL_OPTIONS = {
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch",
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


def load_config() -> dict[str, dict[str, int]]:
    if not CONFIG_PATH.exists():
        return {"music_channels": {}}

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except (json.JSONDecodeError, OSError):
        return {"music_channels": {}}

    config.setdefault("music_channels", {})
    return config


def save_config(config: dict[str, dict[str, int]]) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2)


config = load_config()


def find_ffmpeg_executable() -> str:
    configured = os.getenv("FFMPEG_EXECUTABLE")
    if configured:
        return configured

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        winget_package_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        matches = sorted(winget_package_dir.glob("Gyan.FFmpeg_*/*/bin/ffmpeg.exe"))
        if matches:
            return str(matches[-1])

    return "ffmpeg"


FFMPEG_EXECUTABLE = find_ffmpeg_executable()


def build_ffmpeg_before_options(headers: dict[str, str]) -> str:
    args = [
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
    ]

    user_agent = headers.get("User-Agent")
    if user_agent:
        args.extend(["-user_agent", user_agent])

    header_lines = []
    for name in ("Accept", "Accept-Language", "Origin", "Referer"):
        value = headers.get(name)
        if value:
            header_lines.append(f"{name}: {value}")

    if header_lines:
        args.extend(["-headers", "\r\n".join(header_lines) + "\r\n"])

    return " ".join(shlex.quote(arg) for arg in args)


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    requester: str
    http_headers: dict[str, str]
    thumbnail_url: Optional[str]


class GuildMusicState:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.current: Optional[Track] = None
        self.player_task: Optional[asyncio.Task] = None
        self.last_message_id: Optional[int] = None
        self.last_channel_id: Optional[int] = None


music_states: dict[int, GuildMusicState] = {}


def get_music_state(guild_id: int) -> GuildMusicState:
    state = music_states.get(guild_id)
    if state is None:
        state = GuildMusicState()
        music_states[guild_id] = state
    return state


def get_music_channel_id(guild_id: int) -> Optional[int]:
    channel_id = config["music_channels"].get(str(guild_id))
    return int(channel_id) if channel_id else None


def set_music_channel_id(guild_id: int, channel_id: int) -> None:
    config["music_channels"][str(guild_id)] = channel_id
    save_config(config)


def get_music_output_channel(ctx: commands.Context) -> discord.abc.Messageable:
    if not ctx.guild:
        return ctx.channel

    channel_id = get_music_channel_id(ctx.guild.id)
    if channel_id is None:
        return ctx.channel

    return ctx.guild.get_channel(channel_id) or ctx.channel


async def acknowledge_music_routing(ctx: commands.Context) -> bool:
    if not ctx.guild:
        return False

    channel_id = get_music_channel_id(ctx.guild.id)
    if channel_id is None or ctx.channel.id == channel_id:
        return False

    message = f"I'll handle that in <#{channel_id}>."
    if ctx.interaction:
        await ctx.send(message, ephemeral=True)
        return True

    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass

    return False


async def delete_previous_bot_message(ctx: commands.Context) -> None:
    if not ctx.guild:
        return

    state = get_music_state(ctx.guild.id)
    if not state.last_message_id or not state.last_channel_id:
        return

    channel = bot.get_channel(state.last_channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(state.last_channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    try:
        message = await channel.fetch_message(state.last_message_id)
        if message.author == bot.user:
            await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass
    finally:
        state.last_message_id = None
        state.last_channel_id = None


async def send_clean(ctx: commands.Context, *args, **kwargs) -> discord.Message:
    await delete_previous_bot_message(ctx)
    channel = get_music_output_channel(ctx)
    ephemeral = kwargs.pop("ephemeral", False)

    if channel.id == ctx.channel.id:
        message = await ctx.send(*args, ephemeral=ephemeral, **kwargs)
    else:
        message = await channel.send(*args, **kwargs)

    if ctx.guild:
        state = get_music_state(ctx.guild.id)
        state.last_message_id = message.id
        state.last_channel_id = message.channel.id

    return message


def build_queue_embed(guild_id: int) -> discord.Embed:
    state = get_music_state(guild_id)
    queued_tracks = list(state.queue._queue)

    embed = discord.Embed(title="Music Queue", color=discord.Color.blurple())

    if state.current:
        embed.add_field(
            name="Now playing",
            value=f"[{state.current.title}]({state.current.webpage_url})\nRequested by {state.current.requester}",
            inline=False,
        )
        if state.current.thumbnail_url:
            embed.set_thumbnail(url=state.current.thumbnail_url)

    if queued_tracks:
        lines = []
        for index, track in enumerate(queued_tracks[:10], start=1):
            lines.append(f"`{index}.` [{track.title}]({track.webpage_url}) - {track.requester}")

        remaining = len(queued_tracks) - 10
        if remaining > 0:
            lines.append(f"...and {remaining} more")

        embed.add_field(name="Up next", value="\n".join(lines), inline=False)
    elif not state.current:
        embed.description = "The queue is empty."
    else:
        embed.add_field(name="Up next", value="Nothing queued.", inline=False)

    embed.set_footer(text=f"{len(queued_tracks)} track(s) waiting")
    return embed


class MusicControlView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Play", style=discord.ButtonStyle.success, custom_id="music:play")
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice_client = interaction.guild.voice_client if interaction.guild else None
        if voice_client and voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message("Resumed.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is paused.", ephemeral=True)

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, custom_id="music:pause")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice_client = interaction.guild.voice_client if interaction.guild else None
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message("Paused.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, custom_id="music:stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This only works in a server.", ephemeral=True)
            return

        state = get_music_state(interaction.guild.id)
        while not state.queue.empty():
            state.queue.get_nowait()
            state.queue.task_done()

        voice_client = interaction.guild.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()

        await bot.change_presence(activity=None)
        await interaction.response.send_message("Stopped and cleared the queue.", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary, custom_id="music:skip")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice_client = interaction.guild.voice_client if interaction.guild else None
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
            await interaction.response.send_message("Skipped.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, custom_id="music:queue")
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This only works in a server.", ephemeral=True)
            return

        await interaction.response.send_message(
            embed=build_queue_embed(interaction.guild.id),
            ephemeral=True,
        )


async def extract_track(query: str, requester: str) -> Track:
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))

    if "entries" in data:
        data = data["entries"][0]

    thumbnail_url = data.get("thumbnail")
    thumbnails = data.get("thumbnails") or []
    if thumbnails:
        thumbnail_url = thumbnails[-1].get("url") or thumbnail_url

    return Track(
        title=data.get("title", "Unknown title"),
        webpage_url=data.get("webpage_url", query),
        stream_url=data["url"],
        requester=requester,
        http_headers=data.get("http_headers") or {},
        thumbnail_url=thumbnail_url,
    )


async def ensure_voice(ctx: commands.Context) -> discord.VoiceClient:
    if not ctx.author.voice or not ctx.author.voice.channel:
        raise commands.CommandError("Join a voice channel first.")

    voice_client = ctx.guild.voice_client
    channel = ctx.author.voice.channel

    if voice_client and voice_client.is_connected():
        if voice_client.channel != channel:
            await voice_client.move_to(channel)
        return voice_client

    return await channel.connect()


async def player_loop(ctx: commands.Context) -> None:
    state = get_music_state(ctx.guild.id)

    while True:
        state.current = await state.queue.get()

        voice_client = ctx.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            state.current = None
            continue

        try:
            source = discord.FFmpegPCMAudio(
                state.current.stream_url,
                executable=FFMPEG_EXECUTABLE,
                before_options=build_ffmpeg_before_options(state.current.http_headers),
                **FFMPEG_OPTIONS,
            )
        except Exception as exc:
            await send_clean(ctx, f"Could not start FFmpeg: `{exc}`")
            state.current = None
            state.queue.task_done()
            continue
        done = asyncio.Event()

        def after_play(error: Optional[Exception]) -> None:
            if error:
                print(f"Playback error: {error}")
                asyncio.run_coroutine_threadsafe(
                    send_clean(ctx, f"Playback error: `{error}`"),
                    bot.loop,
                )
            bot.loop.call_soon_threadsafe(done.set)

        voice_client.play(source, after=after_play)
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name=state.current.title[:128],
            )
        )
        embed = discord.Embed(
            title="Now playing",
            description=f"[{state.current.title}]({state.current.webpage_url})",
            color=discord.Color.green(),
        )
        if state.current.thumbnail_url:
            embed.set_thumbnail(url=state.current.thumbnail_url)
        embed.set_footer(text=f"Requested by {state.current.requester}")
        await send_clean(ctx, embed=embed, view=MusicControlView())
        await done.wait()

        state.current = None
        state.queue.task_done()

        if state.queue.empty():
            break

    state.player_task = None
    await bot.change_presence(activity=None)


@bot.event
async def on_ready() -> None:
    global commands_synced, views_registered

    if not views_registered:
        bot.add_view(MusicControlView())
        views_registered = True

    if not commands_synced:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash commands to {guild.name} ({guild.id})", flush=True)
        commands_synced = True

    print(f"Logged in as {bot.user} (prefix: {COMMAND_PREFIX})", flush=True)


@commands.guild_only()
@commands.has_guild_permissions(manage_channels=True)
@bot.hybrid_command(name="setup_music_channel", description="Create or set the bot's dedicated music channel.")
async def setup_music_channel(ctx: commands.Context, name: str = "music-bot") -> None:
    if ctx.interaction:
        await ctx.defer(ephemeral=True)

    existing_channel_id = get_music_channel_id(ctx.guild.id)
    channel = ctx.guild.get_channel(existing_channel_id) if existing_channel_id else None

    if channel is None:
        channel = discord.utils.get(ctx.guild.text_channels, name=name)

    if channel is None:
        channel = await ctx.guild.create_text_channel(
            name=name,
            reason="Dedicated music bot channel",
        )

    set_music_channel_id(ctx.guild.id, channel.id)

    embed = discord.Embed(
        title="Music Bot",
        description="Use this channel for music commands and queue controls.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Commands", value="`/play`, `/queue`, `/pause`, `/resume`, `/skip`, `/stop`, `/leave`")
    await channel.send(embed=embed, view=MusicControlView())
    await ctx.send(f"Music bot channel set to {channel.mention}.", ephemeral=bool(ctx.interaction))


@commands.guild_only()
@bot.hybrid_command(name="play", aliases=["p"], description="Play a YouTube URL or search term.")
async def play(ctx: commands.Context, *, query: str) -> None:
    """Play a YouTube URL or search term."""
    acknowledged = await acknowledge_music_routing(ctx)

    if ctx.interaction and not acknowledged:
        await ctx.defer()

    await ensure_voice(ctx)
    state = get_music_state(ctx.guild.id)

    async with ctx.typing():
        track = await extract_track(query, str(ctx.author.display_name))
        await state.queue.put(track)

    await send_clean(ctx, f"Queued: **{track.title}**", view=MusicControlView())

    if state.player_task is None or state.player_task.done():
        state.player_task = asyncio.create_task(player_loop(ctx))


@commands.guild_only()
@bot.hybrid_command(name="pause", description="Pause the current song.")
async def pause(ctx: commands.Context) -> None:
    await acknowledge_music_routing(ctx)

    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await send_clean(ctx, "Paused.")
    else:
        await send_clean(ctx, "Nothing is playing.")


@commands.guild_only()
@bot.hybrid_command(name="resume", description="Resume the paused song.")
async def resume(ctx: commands.Context) -> None:
    await acknowledge_music_routing(ctx)

    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await send_clean(ctx, "Resumed.")
    else:
        await send_clean(ctx, "Nothing is paused.")


@commands.guild_only()
@bot.hybrid_command(name="skip", description="Skip the current song.")
async def skip(ctx: commands.Context) -> None:
    await acknowledge_music_routing(ctx)

    voice_client = ctx.guild.voice_client
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
        await send_clean(ctx, "Skipped.")
    else:
        await send_clean(ctx, "Nothing is playing.")


@commands.guild_only()
@bot.hybrid_command(name="queue", aliases=["q"], description="Show the current music queue.")
async def queue(ctx: commands.Context) -> None:
    await acknowledge_music_routing(ctx)

    await send_clean(ctx, embed=build_queue_embed(ctx.guild.id), view=MusicControlView())


@commands.guild_only()
@bot.hybrid_command(name="stop", description="Stop playback and clear the queue.")
async def stop(ctx: commands.Context) -> None:
    await acknowledge_music_routing(ctx)

    state = get_music_state(ctx.guild.id)

    while not state.queue.empty():
        state.queue.get_nowait()
        state.queue.task_done()

    voice_client = ctx.guild.voice_client
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()

    await bot.change_presence(activity=None)
    await send_clean(ctx, "Stopped and cleared the queue.")


@commands.guild_only()
@bot.hybrid_command(name="leave", aliases=["disconnect", "dc"], description="Disconnect from voice.")
async def leave(ctx: commands.Context) -> None:
    await acknowledge_music_routing(ctx)

    state = get_music_state(ctx.guild.id)

    while not state.queue.empty():
        state.queue.get_nowait()
        state.queue.task_done()

    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        await bot.change_presence(activity=None)
        await send_clean(ctx, "Disconnected.")
    else:
        await send_clean(ctx, "I am not connected to a voice channel.")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MissingRequiredArgument):
        await send_clean(ctx, f"Missing argument. Try `{COMMAND_PREFIX}{ctx.command} <YouTube URL or search>`.")
    else:
        await send_clean(ctx, str(error))


bot.run(DISCORD_TOKEN)
