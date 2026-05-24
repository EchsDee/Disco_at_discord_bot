import asyncio

import discord
from discord.ext import commands


def register_music_commands(app: dict) -> None:
    bot = app["bot"]
    command_prefix = app["COMMAND_PREFIX"]
    music_control_view = app["MusicControlView"]
    acknowledge_music_routing = app["acknowledge_music_routing"]
    ensure_voice = app["ensure_voice"]
    get_music_state = app["get_music_state"]
    get_music_channel_id = app["get_music_channel_id"]
    set_music_channel_id = app["set_music_channel_id"]
    send_clean_to_channel = app["send_clean_to_channel"]
    send_clean = app["send_clean"]
    extract_tracks = app["extract_tracks"]
    queue_tracks = app["queue_tracks"]
    queued_tracks_message = app["queued_tracks_message"]
    build_queued_tracks_embed = app["build_queued_tracks_embed"]
    player_loop = app["player_loop"]
    build_queue_embed = app["build_queue_embed"]
    log_error = app["log_error"]

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
        await send_clean_to_channel(ctx.guild.id, channel, embed=embed, view=music_control_view())
        await ctx.send(f"Music bot channel set to {channel.mention}.", ephemeral=bool(ctx.interaction))

    @commands.guild_only()
    @bot.hybrid_command(name="play", aliases=["p"], description="Play a YouTube/Spotify URL or search term.")
    async def play(ctx: commands.Context, *, query: str) -> None:
        """Play a YouTube/Spotify URL or search term."""
        acknowledged = await acknowledge_music_routing(ctx)

        if ctx.interaction and not acknowledged:
            await ctx.defer()

        await ensure_voice(ctx)
        state = get_music_state(ctx.guild.id)

        async with ctx.typing():
            try:
                tracks = await extract_tracks(query, str(ctx.author.display_name))
            except Exception as exc:
                log_error(f"Play command failed in {ctx.guild.name} ({ctx.guild.id})", exc)
                raise commands.CommandError(f"Could not load that track: {exc}") from exc
            await queue_tracks(state, tracks)

        await send_clean(
            ctx,
            queued_tracks_message(tracks),
            embed=build_queued_tracks_embed(tracks),
            view=music_control_view(),
        )

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
        await send_clean(ctx, embed=build_queue_embed(ctx.guild.id), view=music_control_view())

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
            await send_clean(ctx, f"Missing argument. Try `{command_prefix}{ctx.command} <YouTube/Spotify URL or search>`.")
        else:
            guild_name = ctx.guild.name if ctx.guild else "DM"
            guild_id = ctx.guild.id if ctx.guild else "none"
            log_error(f"Command error in {guild_name} ({guild_id})", error)
            await send_clean(ctx, str(error))
