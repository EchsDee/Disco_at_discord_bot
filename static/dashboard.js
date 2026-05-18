const servers = document.getElementById("servers");
const serversPage = document.getElementById("serversPage");
const dashboardPage = document.getElementById("dashboardPage");
const soundboardPage = document.getElementById("soundboardPage");
const queuePage = document.getElementById("queuePage");
const adminPage = document.getElementById("adminPage");
const dashboardContent = document.getElementById("dashboardContent");
const soundboardContent = document.getElementById("soundboardContent");
const queueContent = document.getElementById("queueContent");
const navServers = document.getElementById("navServers");
const navDashboard = document.getElementById("navDashboard");
const navSoundboard = document.getElementById("navSoundboard");
const navQueue = document.getElementById("navQueue");
const navAdmin = document.getElementById("navAdmin");
const botStatus = document.getElementById("botStatus");
const botAvatar = document.getElementById("botAvatar");
const adminToast = document.getElementById("adminToast");
const logBox = document.getElementById("logBox");
const loginUsers = document.getElementById("loginUsers");
const dashboardUser = document.getElementById("dashboardUser");
const dashboardAvatar = document.getElementById("dashboardAvatar");
const superuserControls = document.getElementById("superuserControls");
const selectedGuildIcon = document.getElementById("selectedGuildIcon");
const selectedGuildName = document.getElementById("selectedGuildName");
const selectedGuildMeta = document.getElementById("selectedGuildMeta");
const serverMenu = document.getElementById("serverMenu");
const serverPicker = document.getElementById("serverPicker");
const serverPickerChevron = document.getElementById("serverPickerChevron");
const guildNames = {};
const drafts = {};
const selectedVoiceChannels = {};
let currentDashboardUser = null;
let activeView = "dashboard";
let selectedGuildId = localStorage.getItem("selectedGuildId") || "";
let latestGuilds = [];

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
  const isSuperuser = !navAdmin.hidden;
  const allowedViews = ["servers", "dashboard", "soundboard", "queue"];
  const target = view === "admin" && isSuperuser ? "admin" : allowedViews.includes(view) ? view : "dashboard";
  activeView = target;

  serversPage.style.display = target === "servers" ? "grid" : "none";
  dashboardPage.style.display = target === "dashboard" ? "grid" : "none";
  soundboardPage.style.display = target === "soundboard" ? "grid" : "none";
  queuePage.style.display = target === "queue" ? "grid" : "none";
  adminPage.style.display = target === "admin" ? "grid" : "none";

  navServers.classList.toggle("active", target === "servers");
  navDashboard.classList.toggle("active", target === "dashboard");
  navSoundboard.classList.toggle("active", target === "soundboard");
  navQueue.classList.toggle("active", target === "queue");
  navAdmin.classList.toggle("active", target === "admin");

  if (target === "admin") {
    refreshLogs();
    refreshLoginUsers();
  }
}

function toggleNavGroup(groupId) {
  const group = document.getElementById(groupId);
  const chevron = document.getElementById(`${groupId}Chevron`);
  if (!group) return;
  const isOpen = group.classList.toggle("open");
  if (chevron) chevron.textContent = isOpen ? "v" : ">";
}

function toggleServerPicker() {
  serverPicker.classList.toggle("open");
  serverPickerChevron.textContent = serverPicker.classList.contains("open") ? "^" : "v";
}

function selectGuild(guildId) {
  selectedGuildId = String(guildId);
  localStorage.setItem("selectedGuildId", selectedGuildId);
  serverPicker.classList.remove("open");
  serverPickerChevron.textContent = "v";
  renderGuildViews();
}

function selectedGuild() {
  return latestGuilds.find(guild => guild.id === selectedGuildId) || latestGuilds[0] || null;
}

function iconHtml(guild, className) {
  return guild && guild.icon_url
    ? `<img class="${className}" src="${esc(guild.icon_url)}" alt="">`
    : `<div class="${className} server-fallback">${guild ? esc(guild.name.slice(0, 1).toUpperCase()) : "?"}</div>`;
}

function serverMeta(guild) {
  const voice = guild.voice;
  const music = guild.music_channel ? "#" + guild.music_channel.name : "not configured";
  const state = voice.playing ? "playing" : voice.paused ? "paused" : voice.connected ? "connected" : "idle";
  const channel = voice.channel ? " in " + voice.channel : "";
  return `${state}${channel} - music channel: ${music}`;
}

function memberMeta(guild) {
  const total = Number(guild.member_count || 0).toLocaleString();
  if (guild.online_count === null || typeof guild.online_count === "undefined") {
    return `${total} members`;
  }
  const online = Number(guild.online_count || 0).toLocaleString();
  return `${online} online - ${total} members`;
}

function voiceOptions(guild, selectedId = "") {
  if (!guild.voice_channels.length) {
    return `<option value="">No voice channels</option>`;
  }
  return guild.voice_channels.map(channel => {
    const selected = String(channel.id) === String(selectedId) ? " selected" : "";
    return `<option value="${channel.id}"${selected}>${esc(channel.name)}</option>`;
  }).join("");
}

function nowPlaying(guild) {
  if (!guild.current) {
    return `<div class="empty">Nothing is playing.</div>`;
  }
  const thumb = guild.current.thumbnail_url || "";
  return `
    <div class="now">
      ${thumb ? `<img class="thumb" src="${esc(thumb)}" alt="">` : `<div class="thumb server-fallback">Music</div>`}
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
      </div>
    </div>`;
}

function dashboardActions(guild) {
  return `
    <div class="action-panel">
      <h3>Start Music</h3>
      <div class="play-row">
        <input id="play-${guild.id}" placeholder="YouTube URL, playlist, or search">
        <select id="voice-${guild.id}">
          ${voiceOptions(guild, selectedVoiceChannels[`voice-${guild.id}`])}
        </select>
        <button onclick="playFromDashboard('${guild.id}')">Play</button>
      </div>
      <div class="message-row">
        <input id="message-${guild.id}" maxlength="2000" placeholder="Send a message as the bot to ${guild.music_channel ? "#" + esc(guild.music_channel.name) : "the music channel"}">
        <button onclick="sendBotMessage('${guild.id}')">Send</button>
      </div>
      <div class="controls">
        <button class="danger" onclick="leaveServer('${guild.id}')">Remove Bot From Server</button>
      </div>
      <div class="toast" id="server-toast-${guild.id}"></div>
    </div>`;
}

function queueList(guild, interactive = false) {
  if (!guild.queue.length) {
    return `<div class="empty">Queue is empty.</div>`;
  }
  return `<div class="queue ${interactive ? "queue-large" : ""}">` + guild.queue.map((track, index) => `
    <div class="queue-item">
      <div class="queue-number">${index + 1}</div>
      <div>
        <div><a href="${esc(track.url)}" target="_blank">${esc(track.title)}</a></div>
        <div class="meta">Requested by ${esc(track.requester)}</div>
      </div>
      ${interactive ? `<button onclick="playQueueIndex('${guild.id}', ${index})">Play Now</button>` : ""}
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

function renderServerList() {
  servers.innerHTML = latestGuilds.map(guild => `
    <button class="server-row ${guild.id === selectedGuildId ? "active" : ""}" onclick="selectGuild('${guild.id}'); setView('dashboard')" type="button">
      ${iconHtml(guild, "server-row-icon")}
      <span>
        <strong>${esc(guild.name)}</strong>
        <span class="meta">${esc(memberMeta(guild))}</span>
      </span>
    </button>
  `).join("") || `<div class="empty">No servers available yet.</div>`;
}

function renderServerPicker() {
  const guild = selectedGuild();
  if (!guild) {
    selectedGuildIcon.removeAttribute("src");
    selectedGuildIcon.style.display = "none";
    selectedGuildName.textContent = "No servers";
    selectedGuildMeta.textContent = "Invite the bot to a server";
    serverMenu.innerHTML = `<div class="empty">No servers available yet.</div>`;
    return;
  }

  if (guild.icon_url) {
    selectedGuildIcon.src = guild.icon_url;
    selectedGuildIcon.style.display = "block";
  } else {
    selectedGuildIcon.removeAttribute("src");
    selectedGuildIcon.style.display = "none";
  }
  selectedGuildName.textContent = guild.name;
  selectedGuildMeta.textContent = memberMeta(guild);
  serverMenu.innerHTML = latestGuilds.map(item => `
    <button class="server-menu-item ${item.id === guild.id ? "active" : ""}" onclick="selectGuild('${item.id}')" type="button">
      ${iconHtml(item, "server-menu-icon")}
      <span>
        <strong>${esc(item.name)}</strong>
        <span class="meta">${esc(memberMeta(item))}</span>
        <span class="meta">${esc(serverMeta(item))}</span>
      </span>
    </button>
  `).join("");
}

function renderGuildViews() {
  if (!selectedGuildId && latestGuilds[0]) {
    selectedGuildId = latestGuilds[0].id;
    localStorage.setItem("selectedGuildId", selectedGuildId);
  }
  if (selectedGuildId && !latestGuilds.some(guild => guild.id === selectedGuildId)) {
    selectedGuildId = latestGuilds[0] ? latestGuilds[0].id : "";
    if (selectedGuildId) localStorage.setItem("selectedGuildId", selectedGuildId);
  }

  const guild = selectedGuild();
  renderServerList();
  renderServerPicker();

  if (!guild) {
    const empty = `<div class="empty">No server selected.</div>`;
    dashboardContent.innerHTML = empty;
    soundboardContent.innerHTML = empty;
    queueContent.innerHTML = empty;
    return;
  }

  dashboardContent.innerHTML = `
    <section class="server selected-server">
      <div class="server-head">
        ${iconHtml(guild, "server-head-icon")}
        <div class="server-title">
          <h2>${esc(guild.name)}</h2>
          <div class="meta">${esc(serverMeta(guild))}</div>
          <div class="meta">${esc(memberMeta(guild))}</div>
        </div>
      </div>
      <div class="content">
        <div>
          <h3>Now Playing</h3>
          ${nowPlaying(guild)}
        </div>
        <div>
          <h3>Queue Preview</h3>
          ${queueList(guild)}
        </div>
      </div>
      ${dashboardActions(guild)}
    </section>`;

  soundboardContent.innerHTML = `
    <section class="server selected-server">
      <div class="server-head">
        ${iconHtml(guild, "server-head-icon")}
        <div class="server-title">
          <h2>${esc(guild.name)} Soundboard</h2>
          <div class="meta">Saved clips for the selected server.</div>
        </div>
      </div>
      <div class="action-panel">
        <h3>Add Sound</h3>
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
        <select class="compact-select" id="voice-sound-${guild.id}">
          ${voiceOptions(guild, selectedVoiceChannels[`voice-sound-${guild.id}`])}
        </select>
        <div class="toast" id="server-toast-${guild.id}"></div>
      </div>
      <div class="content one-column">
        <div>
          <h3>Saved Sounds</h3>
          ${soundboardPanel(guild)}
        </div>
      </div>
    </section>`;

  queueContent.innerHTML = `
    <section class="server selected-server">
      <div class="server-head">
        ${iconHtml(guild, "server-head-icon")}
        <div class="server-title">
          <h2>${esc(guild.name)} Queue</h2>
          <div class="meta">${guild.current ? "Currently playing: " + guild.current.title : "Nothing is playing."}</div>
        </div>
      </div>
      <div class="content">
        <div>
          <h3>Now Playing</h3>
          ${nowPlaying(guild)}
          <div class="toast" id="toast-${guild.id}"></div>
        </div>
        <div>
          <h3>Upcoming</h3>
          ${queueList(guild, true)}
        </div>
      </div>
    </section>`;
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
  latestGuilds = data.guilds || [];
  latestGuilds.forEach(guild => guildNames[guild.id] = guild.name);

  botStatus.textContent = `${data.bot.name} - ${data.bot.ready ? "online" : "starting"}`;
  if (data.bot.avatar_url) {
    botAvatar.src = data.bot.avatar_url;
  }
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
    setView("dashboard");
  } else {
    setView(currentView());
  }

  renderGuildViews();
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
  logBox.textContent = data.logs.length ? data.logs.join("\n") : "No dashboard logs yet.";
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
  if (!user.can_change_superuser) return `<div class="meta">Local login</div>`;
  if (user.env_superuser) return `<div class="meta">Env superuser</div>`;
  if (currentDashboardUser && user.id === currentDashboardUser.id) return `<div class="meta">You</div>`;
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
    logBox.textContent += `\n${data.output}`;
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
  const toast = document.getElementById(`toast-${guildId}`) || document.getElementById(`server-toast-${guildId}`);
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

async function playQueueIndex(guildId, index) {
  await control(guildId, "play_queue_index", { index });
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
  const select = document.getElementById(`voice-sound-${guildId}`) || document.getElementById(`voice-${guildId}`);
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

document.addEventListener("click", event => {
  if (!serverPicker.contains(event.target)) {
    serverPicker.classList.remove("open");
    serverPickerChevron.textContent = "v";
  }
});

setView("dashboard");
refresh();
setInterval(refresh, 2500);
