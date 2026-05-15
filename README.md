# Python Discord YouTube Music Bot

A small Discord music bot that joins your voice channel and plays audio from YouTube URLs or search terms.

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

## Commands

Commands work as Discord slash commands and as prefix commands.

- `/setup_music_channel [name]`: create or set the dedicated music text channel
- `/play <YouTube URL or search>` or `!play <YouTube URL or search>`: queue a song and start playback
- `/pause` or `!pause`: pause playback
- `/resume` or `!resume`: resume playback
- `/skip` or `!skip`: skip the current song
- `/queue` or `!queue`: show the current queue
- `/stop` or `!stop`: stop playback and clear the queue
- `/leave` or `!leave`: disconnect from voice

Playback messages include buttons for play/resume, pause, stop, skip, and queue.

After `/setup_music_channel`, music command output is routed to that configured channel. Slash commands used elsewhere reply privately to say where the bot will post, and prefix commands used elsewhere are deleted when possible.

## Notes

This bot streams audio with `yt-dlp` and FFmpeg. YouTube may change behavior over time, so update dependencies if playback stops working:

```powershell
pip install -U yt-dlp discord.py PyNaCl
```
