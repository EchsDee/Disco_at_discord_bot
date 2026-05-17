import asyncio
import base64
import hashlib
import hmac
import json
import os
import shlex
import re
import secrets
import subprocess
import threading
import time
import uuid
import webbrowser
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

from aiohttp import ClientSession, web
import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None
    ImageDraw = None


load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
CONFIG_PATH = Path("bot_config.json")
SOUNDBOARD_FILES_DIR = Path("soundboard_files")
MUSIC_IDLE_TIMEOUT_SECONDS = int(os.getenv("MUSIC_IDLE_TIMEOUT_SECONDS", "60"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
DASHBOARD_PUBLIC_URL = os.getenv("DASHBOARD_PUBLIC_URL", "").rstrip("/")
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET") or DISCORD_TOKEN or secrets.token_urlsafe(32)
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DASHBOARD_SUPERUSER_IDS = {
    user_id.strip()
    for user_id in os.getenv("DASHBOARD_SUPERUSER_IDS", "257231933782622210").split(",")
    if user_id.strip()
}
ENABLE_TRAY_ICON = os.getenv("ENABLE_TRAY_ICON", "1") != "0"
MAX_PLAYLIST_TRACKS = int(os.getenv("MAX_PLAYLIST_TRACKS", "50"))
YTDL_EXTRACT_TIMEOUT_SECONDS = int(os.getenv("YTDL_EXTRACT_TIMEOUT_SECONDS", "90"))
YTDL_COOKIE_FILE = os.getenv("YTDL_COOKIE_FILE")
YTDL_FORMAT = os.getenv("YTDL_FORMAT", "bestaudio/best")
YTDL_JS_RUNTIME = os.getenv("YTDL_JS_RUNTIME")

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN. Put it in a .env file or environment variable.")


intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)
commands_synced = False
views_registered = False
dashboard_started = False
tray_started = False
dashboard_runner: Optional[web.AppRunner] = None
recent_logs: deque[str] = deque(maxlen=200)
dashboard_login_user_records: dict[str, dict] = {}


def log_event(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    recent_logs.append(line)
    print(line, flush=True)


def record_dashboard_login(user: dict) -> None:
    user_id = str(user.get("id") or "unknown")
    dashboard_login_user_records[user_id] = {
        "id": user_id,
        "name": user.get("name", user_id),
        "avatar_url": user.get("avatar_url", ""),
        "type": user.get("type", "unknown"),
        "superuser": bool(user.get("superuser")),
        "guild_count": "all" if user.get("guild_ids") == "all" else len(user.get("guild_ids") or []),
        "last_login": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def dashboard_auth_enabled() -> bool:
    return bool(DASHBOARD_PASSWORD or discord_oauth_enabled())


def discord_oauth_enabled() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DASHBOARD_PUBLIC_URL)


def dashboard_public_url() -> str:
    return DASHBOARD_PUBLIC_URL or dashboard_url()


def discord_avatar_url(user_data: dict) -> str:
    user_id = str(user_data.get("id", ""))
    avatar_hash = user_data.get("avatar")
    if user_id and avatar_hash:
        extension = "gif" if str(avatar_hash).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{extension}?size=80"

    try:
        default_index = (int(user_id) >> 22) % 6
    except (TypeError, ValueError):
        default_index = 0
    return f"https://cdn.discordapp.com/embed/avatars/{default_index}.png"


def sign_value(value: str) -> str:
    signature = hmac.new(
        DASHBOARD_SESSION_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{value}.{signature}"


def verify_signed_value(signed_value: str) -> Optional[str]:
    value, separator, signature = signed_value.rpartition(".")
    if separator != "." or not value or not signature:
        return None

    expected = sign_value(value)
    return value if hmac.compare_digest(signed_value, expected) else None


def make_dashboard_session(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return sign_value(encoded)


def get_dashboard_user(request: web.Request) -> Optional[dict]:
    if not dashboard_auth_enabled():
        return {"type": "local", "id": "local", "name": "Local", "superuser": True, "guild_ids": "all"}

    encoded = verify_signed_value(request.cookies.get("dashboard_session", ""))
    if not encoded:
        return None

    try:
        user = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None

    if user.get("type") == "discord":
        user["superuser"] = dashboard_user_id_is_superuser(str(user.get("id", "")))
        if user["superuser"]:
            user["guild_ids"] = "all"
        elif user.get("guild_ids") == "all":
            user["guild_ids"] = []

    return user


def is_dashboard_session_valid(request: web.Request) -> bool:
    return get_dashboard_user(request) is not None


def dashboard_user_is_superuser(request: web.Request) -> bool:
    user = request.get("dashboard_user") or get_dashboard_user(request)
    return bool(user and user.get("superuser"))


def dashboard_user_can_access_guild(request: web.Request, guild_id: int) -> bool:
    user = request.get("dashboard_user") or get_dashboard_user(request)
    if not user:
        return False
    if user.get("superuser"):
        return True
    return str(guild_id) in set(user.get("guild_ids") or [])


def visible_dashboard_guilds(request: web.Request) -> list[discord.Guild]:
    user = request.get("dashboard_user") or get_dashboard_user(request)
    if not user:
        return []
    if user.get("superuser"):
        return list(bot.guilds)

    allowed_guild_ids = set(user.get("guild_ids") or [])
    return [guild for guild in bot.guilds if str(guild.id) in allowed_guild_ids]


@web.middleware
async def dashboard_auth_middleware(request: web.Request, handler):
    public_paths = {"/login", "/api/login", "/auth/discord/start", "/auth/discord/callback"}
    if request.path in public_paths:
        return await handler(request)

    user = get_dashboard_user(request)
    if user:
        request["dashboard_user"] = user
        return await handler(request)

    if request.path.startswith("/api/"):
        return web.json_response({"ok": False, "error": "Login required."}, status=401)

    raise web.HTTPFound("/login")


YTDL_OPTIONS = {
    "format": YTDL_FORMAT,
    "noplaylist": True,
    "default_search": "ytsearch",
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
    "source_address": "0.0.0.0",
}
if YTDL_COOKIE_FILE:
    YTDL_OPTIONS["cookiefile"] = YTDL_COOKIE_FILE
if YTDL_JS_RUNTIME:
    YTDL_OPTIONS["js_runtimes"] = {runtime.strip(): {} for runtime in YTDL_JS_RUNTIME.split(",") if runtime.strip()}

FFMPEG_OPTIONS = {
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
playlist_ytdl = yt_dlp.YoutubeDL(
    {
        **YTDL_OPTIONS,
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "playlistend": MAX_PLAYLIST_TRACKS,
    }
)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"music_channels": {}, "soundboards": {}, "dashboard_superuser_ids": []}

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except (json.JSONDecodeError, OSError):
        return {"music_channels": {}, "soundboards": {}, "dashboard_superuser_ids": []}

    config.setdefault("music_channels", {})
    config.setdefault("soundboards", {})
    config.setdefault("dashboard_superuser_ids", [])
    return config


def save_config(config: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2)


config = load_config()


def dashboard_config_superuser_ids() -> set[str]:
    return {str(user_id).strip() for user_id in config.get("dashboard_superuser_ids", []) if str(user_id).strip()}


def dashboard_all_superuser_ids() -> set[str]:
    return DASHBOARD_SUPERUSER_IDS | dashboard_config_superuser_ids()


def dashboard_user_id_is_superuser(user_id: str) -> bool:
    return str(user_id) in dashboard_all_superuser_ids()


def set_dashboard_config_superuser(user_id: str, enabled: bool) -> None:
    user_id = str(user_id).strip()
    configured = dashboard_config_superuser_ids()
    if enabled:
        configured.add(user_id)
    else:
        configured.discard(user_id)

    config["dashboard_superuser_ids"] = sorted(configured)
    save_config(config)


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
    is_local_file: bool = False


class GuildMusicState:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.current: Optional[Track] = None
        self.player_task: Optional[asyncio.Task] = None
        self.last_message_id: Optional[int] = None
        self.last_channel_id: Optional[int] = None


class DashboardPlaybackContext:
    def __init__(self, guild: discord.Guild, channel: Optional[discord.abc.Messageable]) -> None:
        self.guild = guild
        self.channel = channel

    async def send(self, *args, **kwargs) -> discord.Message:
        if self.channel is None:
            raise commands.CommandError("Set up a music channel first with /setup_music_channel.")

        return await send_clean_to_channel(self.guild.id, self.channel, *args, **kwargs)


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


def get_soundboard(guild_id: int) -> list[dict[str, str]]:
    return config["soundboards"].setdefault(str(guild_id), [])


def add_soundboard_sound(guild_id: int, name: str, query: str) -> dict[str, str]:
    sound = {
        "id": uuid.uuid4().hex,
        "name": name,
        "query": query,
        "source_type": "youtube",
    }
    get_soundboard(guild_id).append(sound)
    save_config(config)
    return sound


def safe_sound_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return cleaned[:80] or "sound"


def add_soundboard_file(guild_id: int, name: str, file_path: Path) -> dict[str, str]:
    sound = {
        "id": uuid.uuid4().hex,
        "name": name,
        "query": str(file_path),
        "source_type": "file",
    }
    get_soundboard(guild_id).append(sound)
    save_config(config)
    return sound


def remove_soundboard_sound(guild_id: int, sound_id: str) -> Optional[dict[str, str]]:
    sounds = get_soundboard(guild_id)
    for index, sound in enumerate(sounds):
        if sound["id"] == sound_id:
            removed = sounds.pop(index)
            if removed.get("source_type") == "file":
                try:
                    Path(removed["query"]).unlink(missing_ok=True)
                except OSError:
                    pass
            save_config(config)
            return removed
    return None


def find_soundboard_sound(guild_id: int, sound_id: str) -> Optional[dict[str, str]]:
    for sound in get_soundboard(guild_id):
        if sound["id"] == sound_id:
            return sound
    return None


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


async def delete_previous_bot_message_for_guild(guild_id: int) -> None:
    state = get_music_state(guild_id)
    if not state.last_message_id or not state.last_channel_id:
        return

    music_channel_id = get_music_channel_id(guild_id)
    if music_channel_id is None or state.last_channel_id != music_channel_id:
        state.last_message_id = None
        state.last_channel_id = None
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


async def delete_previous_bot_message(ctx: commands.Context) -> None:
    if not ctx.guild:
        return

    await delete_previous_bot_message_for_guild(ctx.guild.id)


async def send_clean_to_channel(guild_id: int, channel: discord.abc.Messageable, *args, **kwargs) -> discord.Message:
    should_clean = get_music_channel_id(guild_id) == getattr(channel, "id", None)
    if should_clean:
        await delete_previous_bot_message_for_guild(guild_id)

    message = await channel.send(*args, **kwargs)

    if should_clean:
        state = get_music_state(guild_id)
        state.last_message_id = message.id
        state.last_channel_id = message.channel.id

    return message


async def send_clean(ctx: commands.Context, *args, **kwargs) -> discord.Message:
    channel = get_music_output_channel(ctx)
    ephemeral = kwargs.pop("ephemeral", False)
    should_clean = bool(ctx.guild and get_music_channel_id(ctx.guild.id) == getattr(channel, "id", None))

    if should_clean:
        await delete_previous_bot_message(ctx)

    if channel.id == ctx.channel.id:
        message = await ctx.send(*args, ephemeral=ephemeral, **kwargs)
    else:
        message = await channel.send(*args, **kwargs)

    if should_clean and ctx.guild:
        state = get_music_state(ctx.guild.id)
        state.last_message_id = message.id
        state.last_channel_id = message.channel.id

    return message


def build_queue_embed(guild_id: int) -> discord.Embed:
    state = get_music_state(guild_id)
    queued_tracks = list(state.queue._queue)

    embed = discord.Embed(title="Music Queue", color=discord.Color.blurple())

    if state.current:
        current_title = (
            f"[{state.current.title}]({state.current.webpage_url})"
            if state.current.webpage_url
            else state.current.title
        )
        embed.add_field(
            name="Now playing",
            value=f"{current_title}\nRequested by {state.current.requester}",
            inline=False,
        )
        if state.current.thumbnail_url:
            embed.set_thumbnail(url=state.current.thumbnail_url)

    if queued_tracks:
        lines = []
        for index, track in enumerate(queued_tracks[:10], start=1):
            track_title = f"[{track.title}]({track.webpage_url})" if track.webpage_url else track.title
            lines.append(f"`{index}.` {track_title} - {track.requester}")

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


def dashboard_url() -> str:
    return f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"


def track_to_payload(track: Optional[Track]) -> Optional[dict[str, str]]:
    if track is None:
        return None

    return {
        "title": track.title,
        "url": track.webpage_url,
        "requester": track.requester,
        "thumbnail_url": track.thumbnail_url or "",
        "is_local_file": track.is_local_file,
    }


def guild_to_payload(guild: discord.Guild) -> dict:
    state = get_music_state(guild.id)
    voice_client = guild.voice_client
    music_channel_id = get_music_channel_id(guild.id)
    music_channel = guild.get_channel(music_channel_id) if music_channel_id else None

    return {
        "id": str(guild.id),
        "name": guild.name,
        "icon_url": str(guild.icon.url) if guild.icon else "",
        "music_channel": {
            "id": str(music_channel.id),
            "name": music_channel.name,
        }
        if music_channel
        else None,
        "voice_channels": [
            {
                "id": str(channel.id),
                "name": channel.name,
            }
            for channel in guild.voice_channels
        ],
        "voice": {
            "connected": bool(voice_client and voice_client.is_connected()),
            "playing": bool(voice_client and voice_client.is_playing()),
            "paused": bool(voice_client and voice_client.is_paused()),
            "channel": voice_client.channel.name if voice_client and voice_client.channel else "",
        },
        "current": track_to_payload(state.current),
        "queue": [track_to_payload(track) for track in list(state.queue._queue)],
        "soundboard": get_soundboard(guild.id),
    }


async def dashboard_index(request: web.Request) -> web.Response:
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def dashboard_login_page(request: web.Request) -> web.Response:
    if get_dashboard_user(request):
        raise web.HTTPFound("/")

    html = (
        DASHBOARD_LOGIN_HTML.replace("{{DISCORD_LOGIN_DISPLAY}}", "flex" if discord_oauth_enabled() else "none")
        .replace("{{DIVIDER_DISPLAY}}", "block" if discord_oauth_enabled() and DASHBOARD_PASSWORD else "none")
        .replace("{{PASSWORD_LOGIN_DISPLAY}}", "grid" if DASHBOARD_PASSWORD else "none")
    )
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def dashboard_login(request: web.Request) -> web.Response:
    data = await request.post()
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))

    if not dashboard_auth_enabled():
        return web.json_response({"ok": True, "message": "Dashboard login is disabled."})

    if username != DASHBOARD_USERNAME or not hmac.compare_digest(password, DASHBOARD_PASSWORD):
        log_event("Dashboard login failed.")
        return web.json_response({"ok": False, "error": "Invalid username or password."}, status=401)

    login_user = {
        "type": "password",
        "id": "password",
        "name": username,
        "avatar_url": "",
        "superuser": True,
        "guild_ids": "all",
    }
    response = web.json_response({"ok": True, "message": "Logged in."})
    response.set_cookie(
        "dashboard_session",
        make_dashboard_session(login_user),
        httponly=True,
        path="/",
        samesite="Strict",
        max_age=60 * 60 * 24 * 30,
    )
    record_dashboard_login(login_user)
    log_event(f"Dashboard login succeeded for {username}.")
    return response


async def dashboard_discord_start(request: web.Request) -> web.Response:
    if not discord_oauth_enabled():
        raise web.HTTPNotFound()

    state = secrets.token_urlsafe(24)
    params = urlencode(
        {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": f"{dashboard_public_url()}/auth/discord/callback",
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
        }
    )
    response = web.HTTPFound(f"https://discord.com/oauth2/authorize?{params}")
    response.set_cookie("dashboard_oauth_state", sign_value(state), httponly=True, path="/", samesite="Lax", max_age=600)
    raise response


async def dashboard_discord_callback(request: web.Request) -> web.Response:
    if not discord_oauth_enabled():
        raise web.HTTPNotFound()

    state = request.query.get("state", "")
    code = request.query.get("code", "")
    if not state or not code or verify_signed_value(request.cookies.get("dashboard_oauth_state", "")) != state:
        log_event("Discord dashboard login failed state validation.")
        raise web.HTTPUnauthorized(text="Invalid Discord login state.")

    async with ClientSession() as session:
        token_response = await session.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{dashboard_public_url()}/auth/discord/callback",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_data = await token_response.json()
        if token_response.status != 200:
            log_event(f"Discord dashboard token exchange failed: {token_data}")
            raise web.HTTPUnauthorized(text="Discord login failed.")

        access_token = token_data["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}
        user_response = await session.get("https://discord.com/api/users/@me", headers=headers)
        guilds_response = await session.get("https://discord.com/api/users/@me/guilds", headers=headers)
        user_data = await user_response.json()
        guilds_data = await guilds_response.json()

    if user_response.status != 200 or guilds_response.status != 200:
        log_event("Discord dashboard login failed while loading user guilds.")
        raise web.HTTPUnauthorized(text="Could not load Discord account permissions.")

    discord_user_id = str(user_data["id"])
    superuser = dashboard_user_id_is_superuser(discord_user_id)
    admin_permission_bits = discord.Permissions(administrator=True).value | discord.Permissions(manage_guild=True).value
    allowed_guild_ids = []
    bot_guild_ids = {str(guild.id) for guild in bot.guilds}
    for guild_data in guilds_data:
        guild_id = str(guild_data.get("id"))
        try:
            permissions = int(guild_data.get("permissions", "0"))
        except (TypeError, ValueError):
            permissions = 0
        if guild_id in bot_guild_ids and (permissions & admin_permission_bits):
            allowed_guild_ids.append(guild_id)

    display_name = user_data.get("global_name") or user_data.get("username") or discord_user_id
    avatar_url = discord_avatar_url(user_data)
    login_user = {
        "type": "discord",
        "id": discord_user_id,
        "name": display_name,
        "avatar_url": avatar_url,
        "superuser": superuser,
        "guild_ids": "all" if superuser else allowed_guild_ids,
    }
    response = web.HTTPFound("/")
    response.set_cookie(
        "dashboard_session",
        make_dashboard_session(login_user),
        httponly=True,
        path="/",
        samesite="Strict",
        max_age=60 * 60 * 24 * 30,
    )
    response.del_cookie("dashboard_oauth_state", path="/")
    record_dashboard_login(login_user)
    log_event(f"Discord dashboard login succeeded for {display_name} ({discord_user_id}).")
    raise response


async def dashboard_logout(request: web.Request) -> web.Response:
    response = web.json_response({"ok": True, "message": "Logged out."})
    response.del_cookie("dashboard_session", path="/")
    return response


async def dashboard_status(request: web.Request) -> web.Response:
    user = request.get("dashboard_user") or get_dashboard_user(request) or {}
    return web.json_response(
        {
            "bot": {
                "name": str(bot.user) if bot.user else "Starting...",
                "ready": bot.is_ready(),
                "dashboard_url": dashboard_url(),
            },
            "user": {
                "id": user.get("id", ""),
                "type": user.get("type", ""),
                "name": user.get("name", ""),
                "avatar_url": user.get("avatar_url", ""),
                "superuser": bool(user.get("superuser")),
            },
            "guilds": [guild_to_payload(guild) for guild in visible_dashboard_guilds(request)],
        }
    )


async def dashboard_logs(request: web.Request) -> web.Response:
    if not dashboard_user_is_superuser(request):
        return web.json_response({"ok": False, "error": "Superuser access required."}, status=403)

    return web.json_response({"logs": list(recent_logs)})


async def dashboard_login_users(request: web.Request) -> web.Response:
    if not dashboard_user_is_superuser(request):
        return web.json_response({"ok": False, "error": "Superuser access required."}, status=403)

    env_superuser_ids = DASHBOARD_SUPERUSER_IDS
    config_superuser_ids = dashboard_config_superuser_ids()
    users = sorted(
        (
            {
                **user,
                "superuser": dashboard_user_id_is_superuser(str(user.get("id", ""))),
                "env_superuser": str(user.get("id", "")) in env_superuser_ids,
                "config_superuser": str(user.get("id", "")) in config_superuser_ids,
                "can_change_superuser": user.get("type") == "discord",
            }
            for user in dashboard_login_user_records.values()
        ),
        key=lambda user: user.get("last_login", ""),
        reverse=True,
    )
    return web.json_response({"users": users})


async def dashboard_set_login_user_superuser(request: web.Request) -> web.Response:
    acting_user = request.get("dashboard_user") or get_dashboard_user(request)
    if not dashboard_user_is_superuser(request):
        return web.json_response({"ok": False, "error": "Superuser access required."}, status=403)

    user_id = str(request.match_info["user_id"]).strip()
    if not user_id or user_id in {"local", "password"}:
        return web.json_response({"ok": False, "error": "Only Discord users can be changed."}, status=400)

    data = await request.json()
    enabled = bool(data.get("superuser"))

    if not enabled and user_id in DASHBOARD_SUPERUSER_IDS:
        return web.json_response({"ok": False, "error": "That user is set as a superuser in the .env file."}, status=400)
    if not enabled and acting_user and str(acting_user.get("id")) == user_id:
        return web.json_response({"ok": False, "error": "You cannot remove your own superuser access."}, status=400)

    set_dashboard_config_superuser(user_id, enabled)
    if user_id in dashboard_login_user_records:
        dashboard_login_user_records[user_id]["superuser"] = dashboard_user_id_is_superuser(user_id)
        dashboard_login_user_records[user_id]["guild_count"] = (
            "all"
            if dashboard_login_user_records[user_id]["superuser"]
            else dashboard_login_user_records[user_id].get("guild_count", 0)
        )

    action = "promoted to" if enabled else "removed from"
    log_event(f"Dashboard user {user_id} was {action} superuser by {acting_user.get('name', 'unknown') if acting_user else 'unknown'}.")
    return web.json_response({"ok": True, "message": "Superuser access updated."})


async def dashboard_update_bot(request: web.Request) -> web.Response:
    if not dashboard_user_is_superuser(request):
        return web.json_response({"ok": False, "error": "Superuser access required."}, status=403)

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "pull", "--ff-only"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        log_event("Git update timed out.")
        return web.json_response({"ok": False, "error": "Git update timed out."}, status=504)
    except OSError as exc:
        log_event(f"Git update failed: {exc}")
        return web.json_response({"ok": False, "error": f"Could not run git: {exc}"}, status=500)

    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    output = output or "No output."

    if result.returncode != 0:
        log_event(f"Git update failed with exit code {result.returncode}: {output}")
        return web.json_response({"ok": False, "error": output}, status=500)

    log_event(f"Git update completed: {output}")
    updated = "Already up to date." not in output
    if updated:
        log_event("Update changed files; restarting bot process.")
        bot.loop.call_later(2, lambda: os._exit(0))

    return web.json_response(
        {
            "ok": True,
            "message": "Updated from Git. Restarting..." if updated else "Already up to date.",
            "output": output,
            "restarting": updated,
        }
    )


async def dashboard_control(request: web.Request) -> web.Response:
    guild = bot.get_guild(int(request.match_info["guild_id"]))
    if guild is None:
        return web.json_response({"ok": False, "error": "Unknown server."}, status=404)
    if not dashboard_user_can_access_guild(request, guild.id):
        return web.json_response({"ok": False, "error": "You do not have dashboard access to this server."}, status=403)

    if request.content_type.startswith("multipart/"):
        data = await request.post()
    else:
        data = await request.json()

    action = data.get("action")
    state = get_music_state(guild.id)
    voice_client = guild.voice_client

    if action == "pause":
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            return web.json_response({"ok": True, "message": "Paused."})
        return web.json_response({"ok": False, "error": "Nothing is playing."}, status=400)

    if action == "resume":
        if voice_client and voice_client.is_paused():
            voice_client.resume()
            return web.json_response({"ok": True, "message": "Resumed."})
        return web.json_response({"ok": False, "error": "Nothing is paused."}, status=400)

    if action == "skip":
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
            return web.json_response({"ok": True, "message": "Skipped."})
        return web.json_response({"ok": False, "error": "Nothing is playing."}, status=400)

    if action == "stop":
        while not state.queue.empty():
            state.queue.get_nowait()
            state.queue.task_done()

        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()

        await bot.change_presence(activity=None)
        return web.json_response({"ok": True, "message": "Stopped and cleared the queue."})

    if action == "leave":
        while not state.queue.empty():
            state.queue.get_nowait()
            state.queue.task_done()

        if voice_client and voice_client.is_connected():
            await voice_client.disconnect()

        await bot.change_presence(activity=None)
        return web.json_response({"ok": True, "message": "Disconnected."})

    if action == "send_message":
        message = str(data.get("message", "")).strip()
        if not message:
            return web.json_response({"ok": False, "error": "Message cannot be empty."}, status=400)

        if len(message) > 2000:
            return web.json_response({"ok": False, "error": "Discord messages are limited to 2000 characters."}, status=400)

        channel_id = get_music_channel_id(guild.id)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            return web.json_response(
                {"ok": False, "error": "Set up a music channel first with /setup_music_channel."},
                status=400,
            )

        await send_clean_to_channel(guild.id, channel, message)
        return web.json_response({"ok": True, "message": f"Sent to #{channel.name}."})

    if action == "play":
        query = str(data.get("query", "")).strip()
        if not query:
            return web.json_response({"ok": False, "error": "Enter a YouTube URL or search."}, status=400)

        channel_id = data.get("voice_channel_id")
        voice_channel = guild.get_channel(int(channel_id)) if channel_id else None
        if voice_channel is not None and not isinstance(voice_channel, discord.VoiceChannel):
            voice_channel = None
        if voice_channel is None:
            voice_channels = guild.voice_channels
            voice_channel = voice_channels[0] if voice_channels else None

        if voice_channel is None:
            return web.json_response({"ok": False, "error": "No voice channel found."}, status=400)

        voice_client = guild.voice_client
        if voice_client and voice_client.is_connected():
            if voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)
        else:
            await voice_channel.connect()

        try:
            tracks = await extract_tracks(query, "Dashboard")
        except Exception as exc:
            return web.json_response({"ok": False, "error": f"Could not load that track: {exc}"}, status=400)
        await queue_tracks(state, tracks)

        music_channel_id = get_music_channel_id(guild.id)
        music_channel = guild.get_channel(music_channel_id) if music_channel_id else None
        playback_ctx = DashboardPlaybackContext(guild, music_channel)
        await send_clean_to_channel(
            guild.id,
            music_channel or voice_channel,
            queued_tracks_message(tracks),
            view=MusicControlView(),
        )

        if state.player_task is None or state.player_task.done():
            state.player_task = asyncio.create_task(player_loop(playback_ctx))

        return web.json_response({"ok": True, "message": queued_tracks_message(tracks).replace("**", "")})

    if action == "add_sound":
        name = str(data.get("name", "")).strip()
        query = str(data.get("query", "")).strip()

        if not name or not query:
            return web.json_response({"ok": False, "error": "Sound name and URL/search are required."}, status=400)

        if len(name) > 40:
            return web.json_response({"ok": False, "error": "Sound name must be 40 characters or less."}, status=400)

        add_soundboard_sound(guild.id, name, query)
        return web.json_response({"ok": True, "message": f"Added sound: {name}."})

    if action == "add_sound_file":
        name = str(data.get("name", "")).strip()
        upload = data.get("file")

        if not name or upload is None or not getattr(upload, "file", None):
            return web.json_response({"ok": False, "error": "Sound name and file are required."}, status=400)

        if len(name) > 40:
            return web.json_response({"ok": False, "error": "Sound name must be 40 characters or less."}, status=400)

        original_name = safe_sound_filename(getattr(upload, "filename", "sound"))
        suffix = Path(original_name).suffix[:12]
        guild_dir = SOUNDBOARD_FILES_DIR / str(guild.id)
        guild_dir.mkdir(parents=True, exist_ok=True)
        file_path = guild_dir / f"{uuid.uuid4().hex}{suffix}"

        with file_path.open("wb") as sound_file:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                sound_file.write(chunk)

        add_soundboard_file(guild.id, name, file_path)
        return web.json_response({"ok": True, "message": f"Added file sound: {name}."})

    if action == "remove_sound":
        sound_id = str(data.get("sound_id", ""))
        removed = remove_soundboard_sound(guild.id, sound_id)
        if removed is None:
            return web.json_response({"ok": False, "error": "Sound not found."}, status=404)

        return web.json_response({"ok": True, "message": f"Removed sound: {removed['name']}."})

    if action == "play_sound":
        sound_id = str(data.get("sound_id", ""))
        sound = find_soundboard_sound(guild.id, sound_id)
        if sound is None:
            return web.json_response({"ok": False, "error": "Sound not found."}, status=404)

        query = sound["query"]

        channel_id = data.get("voice_channel_id")
        voice_channel = guild.get_channel(int(channel_id)) if channel_id else None
        if voice_channel is not None and not isinstance(voice_channel, discord.VoiceChannel):
            voice_channel = None
        if voice_channel is None:
            voice_channels = guild.voice_channels
            voice_channel = voice_channels[0] if voice_channels else None

        if voice_channel is None:
            return web.json_response({"ok": False, "error": "No voice channel found."}, status=400)

        voice_client = guild.voice_client
        if voice_client and voice_client.is_connected():
            if voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)
        else:
            await voice_channel.connect()

        if sound.get("source_type") == "file":
            tracks = [
                Track(
                    title=sound["name"],
                    webpage_url="",
                    stream_url=query,
                    requester="Soundboard",
                    http_headers={},
                    thumbnail_url=None,
                    is_local_file=True,
                )
            ]
        else:
            try:
                tracks = await extract_tracks(query, f"Soundboard: {sound['name']}")
            except Exception as exc:
                return web.json_response({"ok": False, "error": f"Could not load that sound: {exc}"}, status=400)
        await queue_tracks(state, tracks)

        music_channel_id = get_music_channel_id(guild.id)
        music_channel = guild.get_channel(music_channel_id) if music_channel_id else None
        playback_ctx = DashboardPlaybackContext(guild, music_channel)
        if len(tracks) == 1:
            message = f"Queued sound: **{sound['name']}**"
        else:
            message = f"Queued sound playlist: **{sound['name']}** ({len(tracks)} tracks)"
        await send_clean_to_channel(guild.id, music_channel or voice_channel, message, view=MusicControlView())

        if state.player_task is None or state.player_task.done():
            state.player_task = asyncio.create_task(player_loop(playback_ctx))

        return web.json_response({"ok": True, "message": message.replace("**", "")})

    if action == "leave_server":
        while not state.queue.empty():
            state.queue.get_nowait()
            state.queue.task_done()

        if voice_client and voice_client.is_connected():
            await voice_client.disconnect()

        await bot.change_presence(activity=None)
        guild_name = guild.name
        await guild.leave()
        return web.json_response({"ok": True, "message": f"Left {guild_name}."})

    return web.json_response({"ok": False, "error": "Unknown action."}, status=400)


async def start_dashboard() -> None:
    global dashboard_runner

    app = web.Application(middlewares=[dashboard_auth_middleware])
    app.router.add_get("/", dashboard_index)
    app.router.add_get("/login", dashboard_login_page)
    app.router.add_post("/api/login", dashboard_login)
    app.router.add_get("/auth/discord/start", dashboard_discord_start)
    app.router.add_get("/auth/discord/callback", dashboard_discord_callback)
    app.router.add_post("/api/logout", dashboard_logout)
    app.router.add_get("/api/status", dashboard_status)
    app.router.add_get("/api/logs", dashboard_logs)
    app.router.add_get("/api/login-users", dashboard_login_users)
    app.router.add_post("/api/login-users/{user_id}/superuser", dashboard_set_login_user_superuser)
    app.router.add_post("/api/update", dashboard_update_bot)
    app.router.add_post("/api/guilds/{guild_id}/control", dashboard_control)

    dashboard_runner = web.AppRunner(app)
    await dashboard_runner.setup()
    site = web.TCPSite(dashboard_runner, DASHBOARD_HOST, DASHBOARD_PORT)
    await site.start()
    log_event(f"Dashboard running at {dashboard_url()}")


def make_tray_image():
    image = Image.new("RGBA", (64, 64), (25, 28, 36, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((12, 12, 52, 52), fill=(88, 101, 242, 255))
    draw.polygon((29, 22, 29, 42, 45, 32), fill=(255, 255, 255, 255))
    return image


def open_dashboard_from_tray(icon=None, item=None) -> None:
    webbrowser.open(dashboard_url())


def quit_from_tray(icon, item=None) -> None:
    asyncio.run_coroutine_threadsafe(bot.close(), bot.loop)
    icon.stop()


def start_tray_icon() -> None:
    if not ENABLE_TRAY_ICON or pystray is None or Image is None or ImageDraw is None:
        return

    icon = pystray.Icon(
        "Disco at Discord",
        make_tray_image(),
        "Disco at Discord",
        menu=pystray.Menu(
            pystray.MenuItem("Open Dashboard", open_dashboard_from_tray, default=True),
            pystray.MenuItem("Quit Bot", quit_from_tray),
        ),
    )
    threading.Thread(target=icon.run, daemon=True).start()


DASHBOARD_LOGIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Disco at Discord Login</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0e1016;
      --panel: #1d2029;
      --line: #333746;
      --text: #f4f5f8;
      --muted: #a8adbd;
      --accent: #58c4dd;
      --discord: #5865f2;
    }
    * { box-sizing: border-box; }
    body {
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      background:
        radial-gradient(circle at 50% 42%, rgba(88, 101, 242, 0.24), transparent 34rem),
        linear-gradient(135deg, #0d1017, #171922 55%, #10131a);
      color: var(--text);
      font-family: Segoe UI, system-ui, sans-serif;
    }
    form {
      width: min(520px, calc(100% - 28px));
      display: grid;
      gap: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(29, 32, 41, 0.92);
      box-shadow: 0 22px 70px rgba(0, 0, 0, 0.44);
      padding: 24px;
    }
    h1 {
      margin: 0 0 4px;
      font-size: 22px;
      text-align: center;
    }
    input, button {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #151821;
      color: var(--text);
      padding: 0 10px;
    }
    button {
      cursor: pointer;
      background: #272b36;
    }
    button:hover { border-color: var(--accent); }
    .password-login {
      display: grid;
      gap: 12px;
    }
    .discord-login {
      width: 96px;
      height: 96px;
      justify-self: center;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: linear-gradient(180deg, #6370ff, var(--discord));
      box-shadow: 0 0 34px rgba(88, 101, 242, 0.54), inset 0 -8px 0 rgba(0, 0, 0, 0.08);
      color: white;
      text-decoration: none;
      transition: transform 120ms ease, filter 120ms ease;
    }
    .discord-login:hover { filter: brightness(1.06); transform: translateY(-1px); }
    .discord-login svg { width: 66px; height: 66px; flex: 0 0 auto; }
    .divider {
      text-align: center;
      color: var(--muted);
      font-size: 13px;
    }
    .status {
      min-height: 18px;
      color: var(--muted);
      font-size: 13px;
    }
  </style>
</head>
<body>
  <form id="loginForm">
    <h1>Disco at Discord</h1>
    <a class="discord-login" href="/auth/discord/start" style="display: {{DISCORD_LOGIN_DISPLAY}};" aria-label="Sign in with Discord">
      <svg viewBox="0 0 245 240" aria-hidden="true" focusable="false">
        <path fill="currentColor" d="M104.4 103.9c-5.7 0-10.2 5-10.2 11.1s4.6 11.1 10.2 11.1c5.7 0 10.3-5 10.2-11.1 0-6.1-4.5-11.1-10.2-11.1Zm36.5 0c-5.7 0-10.2 5-10.2 11.1s4.6 11.1 10.2 11.1c5.7 0 10.2-5 10.2-11.1s-4.5-11.1-10.2-11.1Z"/>
        <path fill="currentColor" d="M189.5 20h-134C44.2 20 35 29.2 35 40.6v135.2c0 11.4 9.2 20.6 20.5 20.6h113.4l-5.3-18.5 12.8 11.9 12.1 11.2 21.5 19V40.6c0-11.4-9.2-20.6-20.5-20.6Zm-38.6 130.6s-3.6-4.3-6.6-8.1c13.1-3.7 18.1-11.9 18.1-11.9-4.1 2.7-8 4.6-11.5 5.9-5 2.1-9.8 3.4-14.5 4.2-9.6 1.8-18.4 1.3-25.9-.1-5.7-1.1-10.6-2.7-14.7-4.2-2.3-.9-4.8-2-7.3-3.4-.3-.2-.6-.3-.9-.5-.2-.1-.3-.2-.4-.3-1.8-1-2.8-1.7-2.8-1.7s4.8 8 17.5 11.8c-3 3.8-6.7 8.3-6.7 8.3-22.1-.7-30.5-15.2-30.5-15.2 0-32.2 14.4-58.3 14.4-58.3 14.4-10.8 28.1-10.5 28.1-10.5l1 1.2c-18 5.2-26.3 13.1-26.3 13.1s2.2-1.2 5.9-2.8c10.7-4.7 19.2-6 22.7-6.3.6-.1 1.1-.2 1.7-.2 6.1-.8 13-1 20.2-.2 9.5 1.1 19.7 3.9 30.1 9.6 0 0-7.9-7.5-24.9-12.7l1.4-1.6s13.7-.3 28.1 10.5c0 0 14.4 26.1 14.4 58.3 0 0-8.5 14.5-30.6 15.2Z"/>
      </svg>
    </a>
    <div class="divider" style="display: {{DIVIDER_DISPLAY}};">or</div>
    <div class="password-login" style="display: {{PASSWORD_LOGIN_DISPLAY}};">
      <input name="username" autocomplete="username" placeholder="Username">
      <input name="password" type="password" autocomplete="current-password" placeholder="Password">
      <button type="submit">Log In</button>
    </div>
    <div class="status" id="status"></div>
  </form>
  <script>
    const form = document.getElementById("loginForm");
    const status = document.getElementById("status");

    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (form.querySelector(".password-login").style.display === "none") return;
      status.textContent = "Logging in...";
      const response = await fetch("/api/login", {
        method: "POST",
        body: new FormData(form)
      });
      const data = await response.json();
      if (response.ok) {
        window.location.href = "/";
      } else {
        status.textContent = data.error || "Login failed.";
      }
    });
  </script>
</body>
</html>
"""


DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Disco at Discord</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #121318;
      --panel: #1d2029;
      --line: #333746;
      --text: #f4f5f8;
      --muted: #a8adbd;
      --accent: #58c4dd;
      --danger: #f26b6b;
      --ok: #7ad88f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Segoe UI, system-ui, sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 22px;
      border-bottom: 1px solid var(--line);
      background: #171922;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    h1 { font-size: 20px; margin: 0; }
    .nav-left,
    .header-right {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }
    .view-select {
      min-height: 34px;
      width: 118px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #20242d;
      color: var(--text);
      padding: 0 8px;
    }
    main {
      width: min(1180px, calc(100% - 28px));
      margin: 18px auto 32px;
      display: grid;
      gap: 14px;
    }
    #servers {
      display: grid;
      gap: 14px;
    }
    .status { color: var(--muted); font-size: 14px; }
    .admin {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 14px;
      display: grid;
      gap: 10px;
    }
    .dashboard-page {
      display: grid;
      gap: 14px;
    }
    .admin-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .user-card {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .user-avatar {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      background: #2a2e3a;
      border: 1px solid var(--line);
      object-fit: cover;
      display: none;
      flex: 0 0 auto;
    }
    .log-box {
      max-height: 260px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #10131a;
      padding: 10px;
      color: var(--muted);
      font: 12px/1.45 Consolas, ui-monospace, monospace;
      white-space: pre-wrap;
    }
    .login-users {
      display: grid;
      gap: 8px;
    }
    .login-user {
      display: grid;
      grid-template-columns: 38px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #20242d;
    }
    .login-user img {
      width: 38px;
      height: 38px;
      border-radius: 50%;
      object-fit: cover;
      background: #2a2e3a;
    }
    .server {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      overflow: hidden;
    }
    .server-head {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px;
      border-bottom: 1px solid var(--line);
    }
    .server-head img {
      width: 38px;
      height: 38px;
      border-radius: 8px;
      background: #2a2e3a;
    }
    .server-title { flex: 1; min-width: 0; }
    .server-title h2 { font-size: 16px; margin: 0 0 4px; }
    .meta { color: var(--muted); font-size: 13px; }
    .content {
      display: grid;
      grid-template-columns: minmax(260px, 1fr) minmax(260px, 1fr);
      gap: 14px;
      padding: 14px;
    }
    .now {
      display: grid;
      grid-template-columns: 96px 1fr;
      gap: 12px;
      align-items: start;
    }
    .thumb {
      width: 96px;
      aspect-ratio: 1;
      object-fit: cover;
      border-radius: 8px;
      background: #2a2e3a;
    }
    h3 {
      margin: 0 0 8px;
      font-size: 13px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0;
    }
    a { color: var(--text); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .song-title { font-weight: 650; line-height: 1.35; }
    .controls {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #272b36;
      color: var(--text);
      min-height: 34px;
      padding: 0 12px;
      cursor: pointer;
    }
    button:hover { border-color: var(--accent); }
    button.danger:hover { border-color: var(--danger); }
    .queue {
      display: grid;
      gap: 8px;
      max-height: 310px;
      overflow: auto;
      padding-right: 4px;
    }
    .queue-item {
      display: grid;
      grid-template-columns: 28px 1fr;
      gap: 8px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #20242d;
    }
    .soundboard {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .sound-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 8px;
      align-items: center;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #20242d;
    }
    .empty {
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 18px;
    }
    .toast {
      color: var(--muted);
      min-height: 18px;
      font-size: 13px;
    }
    .message-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      margin-top: 12px;
    }
    .play-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(160px, 240px) auto;
      gap: 8px;
      margin-top: 12px;
    }
    input, select {
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #151821;
      color: var(--text);
      padding: 0 10px;
      min-width: 0;
    }
    input[type="file"] {
      padding: 6px;
    }
    @media (max-width: 780px) {
      header { align-items: flex-start; flex-direction: column; }
      .header-right { width: 100%; justify-content: space-between; }
      .content { grid-template-columns: 1fr; }
      .message-row { grid-template-columns: 1fr; }
      .play-row { grid-template-columns: 1fr; }
      .sound-item { grid-template-columns: 1fr; }
      .login-user { grid-template-columns: 38px minmax(0, 1fr); }
      .login-user .controls { grid-column: 1 / -1; }
      .admin-head { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div class="nav-left">
      <select class="view-select" id="viewSelect" onchange="setView(this.value)" aria-label="Dashboard page">
        <option value="servers">Servers</option>
        <option value="admin" id="adminOption" hidden>Admin</option>
      </select>
      <h1>Disco at Discord</h1>
    </div>
    <div class="header-right">
      <div class="user-card">
        <img class="user-avatar" id="dashboardAvatar" alt="">
        <div>
          <div class="status" id="botStatus">Loading...</div>
          <div class="meta" id="dashboardUser"></div>
        </div>
      </div>
      <button onclick="logoutDashboard()">Log Out</button>
    </div>
  </header>
  <main>
    <section class="dashboard-page" id="serversPage">
      <div id="servers"></div>
    </section>
    <section class="admin dashboard-page" id="adminPage" style="display: none;">
      <div class="admin-head">
        <div>
          <h3>Admin Tools</h3>
          <div class="meta">Superuser-only controls for maintenance and logs.</div>
          <div class="toast" id="adminToast"></div>
        </div>
        <div class="controls" id="superuserControls">
          <button onclick="updateBot()">Update From Git</button>
          <button onclick="refreshLogs()">Refresh Logs</button>
          <button onclick="refreshLoginUsers()">Refresh Users</button>
        </div>
      </div>
      <h3>Dashboard Logins</h3>
      <div class="login-users" id="loginUsers">Only superusers can see dashboard logins.</div>
      <h3>Logs</h3>
      <div class="log-box" id="logBox">Logs are only visible to superusers.</div>
    </section>
  </main>
  <script>
    const servers = document.getElementById("servers");
    const serversPage = document.getElementById("serversPage");
    const adminPage = document.getElementById("adminPage");
    const viewSelect = document.getElementById("viewSelect");
    const adminOption = document.getElementById("adminOption");
    const botStatus = document.getElementById("botStatus");
    const adminToast = document.getElementById("adminToast");
    const logBox = document.getElementById("logBox");
    const loginUsers = document.getElementById("loginUsers");
    const dashboardUser = document.getElementById("dashboardUser");
    const dashboardAvatar = document.getElementById("dashboardAvatar");
    const superuserControls = document.getElementById("superuserControls");
    const guildNames = {};
    const drafts = {};
    const selectedVoiceChannels = {};
    let currentDashboardUser = null;

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      })[char]);
    }

    function currentView() {
      return viewSelect.value || "servers";
    }

    function setView(view) {
      const target = view === "admin" && !adminOption.hidden ? "admin" : "servers";
      viewSelect.value = target;
      serversPage.style.display = target === "servers" ? "grid" : "none";
      adminPage.style.display = target === "admin" ? "grid" : "none";
      if (target === "admin") {
        refreshLogs();
        refreshLoginUsers();
      }
    }

    function serverMeta(guild) {
      const voice = guild.voice;
      const music = guild.music_channel ? "#" + guild.music_channel.name : "not configured";
      const state = voice.playing ? "playing" : voice.paused ? "paused" : voice.connected ? "connected" : "idle";
      const channel = voice.channel ? " in " + voice.channel : "";
      return `${state}${channel} - music channel: ${music}`;
    }

    function nowPlaying(guild) {
      if (!guild.current) {
        return `
          <div class="empty">Nothing is playing.</div>
          ${serverActions(guild)}
        `;
      }
      const thumb = guild.current.thumbnail_url || "";
      return `
        <div class="now">
          <img class="thumb" src="${esc(thumb)}" alt="">
          <div>
            <div class="song-title"><a href="${esc(guild.current.url)}" target="_blank">${esc(guild.current.title)}</a></div>
            <div class="meta">Requested by ${esc(guild.current.requester)}</div>
            <div class="controls">
              <button onclick="control('${guild.id}', 'resume')">Play</button>
              <button onclick="control('${guild.id}', 'pause')">Pause</button>
              <button onclick="control('${guild.id}', 'skip')">Skip</button>
              <button class="danger" onclick="control('${guild.id}', 'stop')">Stop</button>
              <button class="danger" onclick="control('${guild.id}', 'leave')">Leave</button>
            </div>
            <div class="toast" id="toast-${guild.id}"></div>
          </div>
        </div>
        ${serverActions(guild)}`;
    }

    function serverActions(guild) {
      return `
        <div class="play-row">
          <input id="play-${guild.id}" placeholder="YouTube URL, playlist, or search">
          <select id="voice-${guild.id}">
            ${voiceOptions(guild)}
          </select>
          <button onclick="playFromDashboard('${guild.id}')">Play</button>
        </div>
        <div class="message-row">
          <input id="message-${guild.id}" maxlength="2000" placeholder="Send a message as the bot to ${guild.music_channel ? "#" + esc(guild.music_channel.name) : "the music channel"}">
          <button onclick="sendBotMessage('${guild.id}')">Send</button>
        </div>
        <div class="play-row">
          <input id="sound-name-${guild.id}" maxlength="40" placeholder="Sound name">
          <input id="sound-query-${guild.id}" placeholder="YouTube URL, playlist, or search">
          <button onclick="addSound('${guild.id}')">Add Sound</button>
        </div>
        <div class="play-row">
          <input id="sound-file-name-${guild.id}" maxlength="40" placeholder="File sound name">
          <input id="sound-file-${guild.id}" type="file" accept="audio/*,video/*">
          <button onclick="addSoundFile('${guild.id}')">Add File</button>
        </div>
        <div class="controls">
          <button class="danger" onclick="leaveServer('${guild.id}')">Remove Bot From Server</button>
        </div>
        <div class="toast" id="server-toast-${guild.id}"></div>
      `;
    }

    function voiceOptions(guild) {
      if (!guild.voice_channels.length) {
        return `<option value="">No voice channels</option>`;
      }
      return guild.voice_channels.map(channel => `<option value="${channel.id}">${esc(channel.name)}</option>`).join("");
    }

    function queueList(guild) {
      if (!guild.queue.length) {
        return `<div class="empty">Queue is empty.</div>`;
      }
      return `<div class="queue">` + guild.queue.map((track, index) => `
        <div class="queue-item">
          <div class="meta">${index + 1}</div>
          <div>
            <div><a href="${esc(track.url)}" target="_blank">${esc(track.title)}</a></div>
            <div class="meta">Requested by ${esc(track.requester)}</div>
          </div>
        </div>
      `).join("") + `</div>`;
    }

    function soundboardPanel(guild) {
      if (!guild.soundboard.length) {
        return `<div class="empty">No saved sounds yet.</div>`;
      }

      return `<div class="soundboard">` + guild.soundboard.map(sound => `
        <div class="sound-item">
          <div>
            <div class="song-title">${esc(sound.name)}</div>
            <div class="meta">${sound.source_type === "file" ? "Local file" : esc(sound.query)}</div>
          </div>
          <button onclick="playSound('${guild.id}', '${sound.id}')">Play</button>
          <button class="danger" onclick="removeSound('${guild.id}', '${sound.id}')">Remove</button>
        </div>
      `).join("") + `</div>`;
    }

    function render(data) {
      const active = document.activeElement;
      const focusState = active && active.id ? {
        id: active.id,
        start: active.selectionStart,
        end: active.selectionEnd
      } : null;
      saveDrafts();
      currentDashboardUser = data.user || null;
      botStatus.textContent = `${data.bot.name} - ${data.bot.ready ? "online" : "starting"}`;
      dashboardUser.textContent = data.user && data.user.name ? `Logged in as ${data.user.name}` : "";
      if (data.user && data.user.avatar_url) {
        dashboardAvatar.src = data.user.avatar_url;
        dashboardAvatar.style.display = "block";
      } else {
        dashboardAvatar.removeAttribute("src");
        dashboardAvatar.style.display = "none";
      }
      const isSuperuser = Boolean(data.user && data.user.superuser);
      adminOption.hidden = !isSuperuser;
      superuserControls.style.display = isSuperuser ? "flex" : "none";
      if (!isSuperuser && currentView() === "admin") {
        setView("servers");
      } else {
        setView(currentView());
      }
      data.guilds.forEach(guild => guildNames[guild.id] = guild.name);
      servers.innerHTML = data.guilds.map(guild => `
        <section class="server">
          <div class="server-head">
            ${guild.icon_url ? `<img src="${esc(guild.icon_url)}" alt="">` : `<img alt="">`}
            <div class="server-title">
              <h2>${esc(guild.name)}</h2>
              <div class="meta">${esc(serverMeta(guild))}</div>
            </div>
          </div>
          <div class="content">
            <div>
              <h3>Now Playing</h3>
              ${nowPlaying(guild)}
            </div>
            <div>
              <h3>Queue</h3>
              ${queueList(guild)}
              <h3 style="margin-top: 16px;">Soundboard</h3>
              ${soundboardPanel(guild)}
            </div>
          </div>
        </section>
      `).join("") || `<div class="empty">No servers available yet.</div>`;
      restoreDrafts();
      restoreFocus(focusState);
    }

    function saveDrafts() {
      document.querySelectorAll("input[id^='play-'], input[id^='message-'], input[id^='sound-name-'], input[id^='sound-query-'], input[id^='sound-file-name-']").forEach(input => {
        drafts[input.id] = input.value;
      });
      document.querySelectorAll("select[id^='voice-']").forEach(select => {
        selectedVoiceChannels[select.id] = select.value;
      });
    }

    function restoreFocus(focusState) {
      if (!focusState) return;
      const element = document.getElementById(focusState.id);
      if (!element) return;

      element.focus();
      if (
        typeof element.setSelectionRange === "function" &&
        typeof focusState.start === "number" &&
        typeof focusState.end === "number"
      ) {
        element.setSelectionRange(focusState.start, focusState.end);
      }
    }

    function restoreDrafts() {
      Object.entries(drafts).forEach(([id, value]) => {
        const input = document.getElementById(id);
        if (input) input.value = value;
      });
      Object.entries(selectedVoiceChannels).forEach(([id, value]) => {
        const select = document.getElementById(id);
        if (select && [...select.options].some(option => option.value === value)) {
          select.value = value;
        }
      });
    }

    async function refresh() {
      const response = await fetch("/api/status");
      const data = await response.json();
      render(data);
    }

    async function refreshLogs() {
      const response = await fetch("/api/logs");
      if (response.status === 403) {
        logBox.textContent = "Logs are only visible to superusers.";
        return;
      }
      const data = await response.json();
      logBox.textContent = data.logs.length ? data.logs.join("\\n") : "No dashboard logs yet.";
      logBox.scrollTop = logBox.scrollHeight;
    }

    async function refreshLoginUsers() {
      const response = await fetch("/api/login-users");
      if (response.status === 403) {
        loginUsers.textContent = "Only superusers can see dashboard logins.";
        return;
      }
      const data = await response.json();
      if (!data.users.length) {
        loginUsers.innerHTML = `<div class="empty">No dashboard logins recorded since the bot started.</div>`;
        return;
      }

      loginUsers.innerHTML = data.users.map(user => `
        <div class="login-user">
          ${user.avatar_url ? `<img src="${esc(user.avatar_url)}" alt="">` : `<img alt="">`}
          <div>
            <div class="song-title">${esc(user.name)}</div>
            <div class="meta">${esc(user.type)} - ${esc(user.id)} - ${user.superuser ? "superuser" : "server admin"} - servers: ${esc(user.guild_count)}</div>
            <div class="meta">Last login: ${esc(user.last_login)}</div>
          </div>
          ${superuserAction(user)}
        </div>
      `).join("");
    }

    function superuserAction(user) {
      if (!user.can_change_superuser) {
        return `<div class="meta">Local login</div>`;
      }
      if (user.env_superuser) {
        return `<div class="meta">Env superuser</div>`;
      }
      if (currentDashboardUser && user.id === currentDashboardUser.id) {
        return `<div class="meta">You</div>`;
      }
      if (user.superuser) {
        return `<div class="controls"><button class="danger" onclick="setLoginUserSuperuser('${esc(user.id)}', false)">Remove Superuser</button></div>`;
      }
      return `<div class="controls"><button onclick="setLoginUserSuperuser('${esc(user.id)}', true)">Make Superuser</button></div>`;
    }

    async function setLoginUserSuperuser(userId, superuser) {
      adminToast.textContent = superuser ? "Promoting user..." : "Removing superuser access...";
      const response = await fetch(`/api/login-users/${encodeURIComponent(userId)}/superuser`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ superuser })
      });
      const data = await response.json();
      adminToast.textContent = data.message || data.error || "";
      await refreshLoginUsers();
    }

    async function updateBot() {
      if (!confirm("Update the bot from Git and restart if files changed?")) return;
      adminToast.textContent = "Updating from Git...";
      const response = await fetch("/api/update", { method: "POST" });
      const data = await response.json();
      adminToast.textContent = data.message || data.error || "";
      if (data.output) {
        logBox.textContent += `\\n${data.output}`;
        logBox.scrollTop = logBox.scrollHeight;
      }
    }

    async function logoutDashboard() {
      await fetch("/api/logout", { method: "POST" });
      window.location.href = "/login";
    }

    async function control(guildId, action, extra = {}) {
      const toast = document.getElementById(`toast-${guildId}`);
      if (toast) toast.textContent = "Working...";
      const response = await fetch(`/api/guilds/${guildId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...extra })
      });
      const data = await response.json();
      if (toast) toast.textContent = data.message || data.error || "";
      await refresh();
    }

    async function sendBotMessage(guildId) {
      const input = document.getElementById(`message-${guildId}`);
      const toast = document.getElementById(`server-toast-${guildId}`);
      const message = input ? input.value.trim() : "";
      if (!message) {
        if (toast) toast.textContent = "Write a message first.";
        return;
      }

      if (toast) toast.textContent = "Sending...";
      const response = await fetch(`/api/guilds/${guildId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "send_message", message })
      });
      const data = await response.json();
      if (toast) toast.textContent = data.message || data.error || "";
      if (response.ok && input) {
        input.value = "";
        drafts[input.id] = "";
      }
    }

    async function playFromDashboard(guildId) {
      const input = document.getElementById(`play-${guildId}`);
      const select = document.getElementById(`voice-${guildId}`);
      const toast = document.getElementById(`server-toast-${guildId}`);
      const query = input ? input.value.trim() : "";

      if (!query) {
        if (toast) toast.textContent = "Enter a YouTube URL or search.";
        return;
      }

      if (toast) toast.textContent = "Queuing...";
      const response = await fetch(`/api/guilds/${guildId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "play",
          query,
          voice_channel_id: select ? select.value : ""
        })
      });
      const data = await response.json();
      if (toast) toast.textContent = data.message || data.error || "";
      if (response.ok && input) {
        input.value = "";
        drafts[input.id] = "";
      }
      await refresh();
    }

    async function addSound(guildId) {
      const nameInput = document.getElementById(`sound-name-${guildId}`);
      const queryInput = document.getElementById(`sound-query-${guildId}`);
      const toast = document.getElementById(`server-toast-${guildId}`);
      const name = nameInput ? nameInput.value.trim() : "";
      const query = queryInput ? queryInput.value.trim() : "";

      if (!name || !query) {
        if (toast) toast.textContent = "Add a sound name and URL/search.";
        return;
      }

      if (toast) toast.textContent = "Adding sound...";
      const response = await fetch(`/api/guilds/${guildId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "add_sound", name, query })
      });
      const data = await response.json();
      if (toast) toast.textContent = data.message || data.error || "";
      if (response.ok) {
        if (nameInput) {
          nameInput.value = "";
          drafts[nameInput.id] = "";
        }
        if (queryInput) {
          queryInput.value = "";
          drafts[queryInput.id] = "";
        }
      }
      await refresh();
    }

    async function addSoundFile(guildId) {
      const nameInput = document.getElementById(`sound-file-name-${guildId}`);
      const fileInput = document.getElementById(`sound-file-${guildId}`);
      const toast = document.getElementById(`server-toast-${guildId}`);
      const name = nameInput ? nameInput.value.trim() : "";
      const file = fileInput && fileInput.files.length ? fileInput.files[0] : null;

      if (!name || !file) {
        if (toast) toast.textContent = "Add a file sound name and choose a file.";
        return;
      }

      const form = new FormData();
      form.append("action", "add_sound_file");
      form.append("name", name);
      form.append("file", file);

      if (toast) toast.textContent = "Uploading sound...";
      const response = await fetch(`/api/guilds/${guildId}/control`, {
        method: "POST",
        body: form
      });
      const data = await response.json();
      if (toast) toast.textContent = data.message || data.error || "";
      if (response.ok) {
        if (nameInput) {
          nameInput.value = "";
          drafts[nameInput.id] = "";
        }
        if (fileInput) fileInput.value = "";
      }
      await refresh();
    }

    async function playSound(guildId, soundId) {
      const select = document.getElementById(`voice-${guildId}`);
      const toast = document.getElementById(`server-toast-${guildId}`);
      if (toast) toast.textContent = "Queuing sound...";
      const response = await fetch(`/api/guilds/${guildId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "play_sound",
          sound_id: soundId,
          voice_channel_id: select ? select.value : ""
        })
      });
      const data = await response.json();
      if (toast) toast.textContent = data.message || data.error || "";
      await refresh();
    }

    async function removeSound(guildId, soundId) {
      const toast = document.getElementById(`server-toast-${guildId}`);
      if (toast) toast.textContent = "Removing sound...";
      const response = await fetch(`/api/guilds/${guildId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "remove_sound", sound_id: soundId })
      });
      const data = await response.json();
      if (toast) toast.textContent = data.message || data.error || "";
      await refresh();
    }

    async function leaveServer(guildId) {
      const guildName = guildNames[guildId] || "this server";
      if (!confirm(`Remove the bot from ${guildName}? You will need to invite it again later.`)) {
        return;
      }

      const toast = document.getElementById(`server-toast-${guildId}`);
      if (toast) toast.textContent = "Leaving server...";
      const response = await fetch(`/api/guilds/${guildId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "leave_server" })
      });
      const data = await response.json();
      if (toast) toast.textContent = data.message || data.error || "";
      await refresh();
    }

    setView("servers");
    refresh();
    setInterval(refresh, 2500);
  </script>
</body>
</html>
"""


def looks_like_playlist_query(query: str) -> bool:
    parsed = urlparse(query)
    if not parsed.scheme or not parsed.netloc:
        return False

    query_values = parse_qs(parsed.query)
    return "list" in query_values or "/playlist" in parsed.path


def track_from_data(data: dict, query: str, requester: str) -> Track:
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


async def extract_tracks(query: str, requester: str) -> list[Track]:
    loop = asyncio.get_running_loop()
    extractor = playlist_ytdl if looks_like_playlist_query(query) else ytdl
    log_event(f"Extracting media for {requester}: {query}")
    data = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: extractor.extract_info(query, download=False)),
        timeout=YTDL_EXTRACT_TIMEOUT_SECONDS,
    )

    if "entries" in data:
        entries = [entry for entry in data["entries"] if entry]
        if looks_like_playlist_query(query):
            tracks = []
            for index, entry in enumerate(entries[:MAX_PLAYLIST_TRACKS], start=1):
                entry_url = entry.get("webpage_url") or entry.get("url")
                if not entry_url:
                    continue

                log_event(f"Extracting playlist item {index}/{len(entries)} for {requester}: {entry_url}")
                item_data = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda item_url=entry_url: ytdl.extract_info(item_url, download=False)),
                    timeout=YTDL_EXTRACT_TIMEOUT_SECONDS,
                )
                tracks.append(track_from_data(item_data, entry_url, requester))

            if not tracks:
                raise commands.CommandError("No playable tracks found in that playlist.")
            log_event(f"Extracted playlist with {len(tracks)} tracks for {requester}")
            return tracks

        if not entries:
            raise commands.CommandError("No playable tracks found.")
        data = entries[0]

    track = track_from_data(data, query, requester)
    log_event(f"Extracted track for {requester}: {track.title}")
    return [track]


async def extract_track(query: str, requester: str) -> Track:
    tracks = await extract_tracks(query, requester)
    return tracks[0]


async def queue_tracks(state: GuildMusicState, tracks: list[Track]) -> None:
    for track in tracks:
        await state.queue.put(track)


def queued_tracks_message(tracks: list[Track]) -> str:
    if len(tracks) == 1:
        return f"Queued: **{tracks[0].title}**"

    return f"Queued **{len(tracks)}** playlist tracks. First up: **{tracks[0].title}**"


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
        log_event(f"Starting playback: {state.current.title}")

        voice_client = ctx.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            state.current = None
            continue

        try:
            before_options = None if state.current.is_local_file else build_ffmpeg_before_options(state.current.http_headers)
            source = discord.FFmpegPCMAudio(
                state.current.stream_url,
                executable=FFMPEG_EXECUTABLE,
                before_options=before_options,
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
                log_event(f"Playback error: {error}")
                asyncio.run_coroutine_threadsafe(
                    send_clean(ctx, f"Playback error: `{error}`"),
                    bot.loop,
                )
            bot.loop.call_soon_threadsafe(done.set)

        voice_client.play(source, after=after_play)
        log_event(f"FFmpeg playback started: {state.current.title}")
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name=state.current.title[:128],
            )
        )
        embed = discord.Embed(
            title="Now playing",
            description=f"[{state.current.title}]({state.current.webpage_url})" if state.current.webpage_url else state.current.title,
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

    if MUSIC_IDLE_TIMEOUT_SECONDS > 0:
        await asyncio.sleep(MUSIC_IDLE_TIMEOUT_SECONDS)

    voice_client = ctx.guild.voice_client
    if (
        voice_client
        and voice_client.is_connected()
        and not voice_client.is_playing()
        and not voice_client.is_paused()
        and state.queue.empty()
    ):
        await voice_client.disconnect()


@bot.event
async def on_ready() -> None:
    global commands_synced, views_registered, dashboard_started, tray_started

    if not views_registered:
        bot.add_view(MusicControlView())
        views_registered = True

    if not dashboard_started:
        await start_dashboard()
        dashboard_started = True

    if not tray_started:
        start_tray_icon()
        tray_started = True

    if not commands_synced:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            log_event(f"Synced {len(synced)} slash commands to {guild.name} ({guild.id})")
        commands_synced = True

    log_event(f"Logged in as {bot.user} (prefix: {COMMAND_PREFIX})")


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
    await send_clean_to_channel(ctx.guild.id, channel, embed=embed, view=MusicControlView())
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
        try:
            tracks = await extract_tracks(query, str(ctx.author.display_name))
        except Exception as exc:
            raise commands.CommandError(f"Could not load that track: {exc}") from exc
        await queue_tracks(state, tracks)

    await send_clean(ctx, queued_tracks_message(tracks), view=MusicControlView())

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
