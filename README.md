# Python Discord YouTube Music Bot

A small Discord music bot that joins your voice channel and plays audio from YouTube URLs, Spotify links, playlists, or search terms.

## Setup

1. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install Python dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Install FFmpeg and make sure `ffmpeg` is available in your terminal:

   ```powershell
   ffmpeg -version
   ```

   On Windows, one simple option is:

   ```powershell
   winget install Gyan.FFmpeg
   ```

4. Copy `.env.example` to `.env` and add your bot token:

   ```powershell
   Copy-Item .env.example .env
   ```

5. In the Discord Developer Portal, enable **Message Content Intent** for your bot.

6. Invite the bot with these permissions:

   - View Channels
   - Send Messages
   - Connect
   - Speak
   - Use Voice Activity

7. Run it:

   ```powershell
   python bot.py
   ```

   On Windows, you can also double-click `Start Bot.bat`.

## Commands

Commands work as Discord slash commands and as prefix commands.

- `/setup_music_channel [name]`: create or set the dedicated music text channel
- `/play <YouTube/Spotify URL, playlist, or search>` or `!play <YouTube/Spotify URL, playlist, or search>`: queue a song or playlist and start playback
- `/pause` or `!pause`: pause playback
- `/resume` or `!resume`: resume playback
- `/skip` or `!skip`: skip the current song
- `/queue` or `!queue`: show the current queue
- `/stop` or `!stop`: stop playback and clear the queue
- `/leave` or `!leave`: disconnect from voice

Playback messages include buttons for play/resume, pause, stop, skip, and queue.

After `/setup_music_channel`, music command output is routed to that configured channel. Slash commands used elsewhere reply privately to say where the bot will post, and prefix commands used elsewhere are deleted when possible.

When the queue finishes, the bot leaves voice after `MUSIC_IDLE_TIMEOUT_SECONDS` seconds. Set it to `0` to leave immediately.

## Desktop Dashboard

The bot also starts a local dashboard at:

```text
http://127.0.0.1:8765
```

On Windows, it adds a tray icon named **Disco at Discord**. Use the tray menu to open the dashboard or quit the bot.

Dashboard settings:

- `DASHBOARD_HOST=127.0.0.1`: keep the dashboard local to your machine
- `DASHBOARD_PORT=8765`: local dashboard port
- `DASHBOARD_USERNAME=admin`: dashboard login username when `DASHBOARD_PASSWORD` is set
- `DASHBOARD_PASSWORD=`: dashboard login password; leave blank to disable login
- `DASHBOARD_SESSION_SECRET=`: optional cookie signing secret; set a long random value for public dashboards
- `DASHBOARD_PUBLIC_URL=`: public dashboard base URL for Discord OAuth redirects
- `DISCORD_CLIENT_ID=` and `DISCORD_CLIENT_SECRET=`: Discord OAuth application credentials
- `DASHBOARD_SUPERUSER_IDS=your_discord_user_id_here`: comma-separated Discord user IDs that can view logs and update from Git
- `ENABLE_TRAY_ICON=1`: set to `0` to disable the tray icon
- `MAX_PLAYLIST_TRACKS=50`: maximum songs to add from one playlist link
- `YTDL_EXTRACT_TIMEOUT_SECONDS=90`: maximum time to wait for yt-dlp extraction
- `YTDL_COOKIE_FILE=`: optional path to a Netscape-format YouTube cookies file for hosted servers
- `YTDL_FORMAT=bestaudio/best`: yt-dlp format selector
- `YTDL_JS_RUNTIME=`: optional JavaScript runtime for yt-dlp challenge solving, for example `deno`
- `SPOTIFY_CLIENT_ID=` and `SPOTIFY_CLIENT_SECRET=`: optional Spotify app credentials for Spotify track, album, and playlist links
- `SPOTIFY_MARKET=US`: Spotify market used when resolving available tracks

The dashboard shows each server, current song, voice status, queue, saved soundboard, and recent bot logs. It can pause, resume, skip, stop, disconnect from voice, start music from a URL/search/playlist, save soundboard buttons from YouTube or Spotify searches/URLs/playlists or uploaded local files, play saved sounds, send a message as the bot to the configured music channel, update the bot from Git, or remove the bot from a server.

Uploaded soundboard files are stored in `soundboard_files/`, which is ignored by Git.

## Project Structure

- `main.py`: application entrypoint
- `bot.py`: bot setup, dashboard API, playback state, and shared helpers
- `commands.py`: Discord slash/prefix command registration
- `templates/dashboard.html`: dashboard markup
- `static/dashboard.css`: dashboard styles
- `static/dashboard.js`: dashboard browser logic

## Notes

This bot streams audio with `yt-dlp` and FFmpeg. YouTube may change behavior over time, so update dependencies if playback stops working:

```powershell
pip install -U yt-dlp discord.py PyNaCl
```
