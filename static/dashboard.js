    const servers = document.getElementById("servers");
    const serversPage = document.getElementById("serversPage");
    const adminPage = document.getElementById("adminPage");
    const navServers = document.getElementById("navServers");
    const navDashboard = document.getElementById("navDashboard");
    const navAdmin = document.getElementById("navAdmin");
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
    let activeView = "servers";

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
      return activeView;
    }

    function setView(view) {
      const target = view === "admin" && !navAdmin.hidden ? "admin" : "servers";
      activeView = target;
      serversPage.style.display = target === "servers" ? "grid" : "none";
      adminPage.style.display = target === "admin" ? "grid" : "none";
      navServers.classList.remove("active");
      navDashboard.classList.toggle("active", target === "servers");
      navAdmin.classList.toggle("active", target === "admin");
      if (target === "admin") {
        refreshLogs();
        refreshLoginUsers();
      }
    }

    function toggleNavGroup(groupId) {
      const group = document.getElementById(groupId);
      const chevron = document.getElementById(`${groupId}Chevron`);
      if (!group) {
        return;
      }
      const isOpen = group.classList.toggle("open");
      if (chevron) {
        chevron.textContent = isOpen ? "⌄" : "›";
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
      navAdmin.hidden = !isSuperuser;
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

    async function restartBot() {
      if (!confirm("Restart the bot now? Music playback will stop for a moment.")) return;
      adminToast.textContent = "Restarting bot...";
      const response = await fetch("/api/restart", { method: "POST" });
      const data = await response.json();
      adminToast.textContent = data.message || data.error || "";
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
