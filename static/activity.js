import { DiscordSDK } from "/static/vendor/discord-embedded-app-sdk/index.mjs";

const config = window.ACTIVITY_CONFIG || {};
const params = new URLSearchParams(location.search);
const captureMode = Boolean(config.captureMode);
const peerId = crypto.randomUUID();
const peers = new Map();
const knownPeers = new Set();
const remoteStreams = new Map();

let discordSdk = null;
let auth = null;
let roomId = "";
let socket = null;
let localStream = null;
let signalToken = "";

const statusPill = document.getElementById("statusPill");
const localVideo = document.getElementById("localVideo");
const emptyState = document.getElementById("emptyState");
const shareButton = document.getElementById("shareButton");
const stopButton = document.getElementById("stopButton");
const roomLabel = document.getElementById("roomLabel");
const remoteGrid = document.getElementById("remoteGrid");
const notice = document.getElementById("notice");

function setStatus(text) {
  statusPill.textContent = text;
}

function showStreamInMainPanel(stream) {
  localVideo.muted = true;
  localVideo.srcObject = stream;
  emptyState.style.display = "none";
  localVideo.play().catch(() => {
    notice.textContent = "The stream is ready. Click inside the Activity if Discord pauses playback.";
  });
}

function refreshEmptyState() {
  if (localStream || remoteStreams.size > 0) {
    emptyState.style.display = "none";
    return;
  }

  localVideo.srcObject = null;
  emptyState.style.display = "";
}

function send(payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ peerId, ...payload }));
}

function websocketUrl(room, token) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/activity/ws/${encodeURIComponent(room)}?token=${encodeURIComponent(token)}`;
}

function captureUrl() {
  const url = new URL("/activity/capture", config.publicUrl || location.origin);
  url.searchParams.set("room", roomId);
  url.searchParams.set("token", signalToken);
  return url.toString();
}

async function openCaptureWindow() {
  if (!roomId || !signalToken) {
    notice.textContent = "Activity is still connecting. Try again in a moment.";
    return;
  }

  const url = captureUrl();
  if (discordSdk?.commands?.openExternalLink) {
    await discordSdk.commands.openExternalLink({ url });
    return;
  }

  window.open(url, "_blank", "noopener,noreferrer");
}

async function authenticateActivity() {
  if (!config.clientId) {
    throw new Error("Activity client ID is not configured.");
  }

  discordSdk = new DiscordSDK(config.clientId);
  await discordSdk.ready();

  roomId = discordSdk.channelId || new URLSearchParams(location.search).get("room") || "default";
  roomLabel.textContent = `Room: ${roomId}`;

  const { code } = await discordSdk.commands.authorize({
    client_id: config.clientId,
    response_type: "code",
    state: "",
    prompt: "none",
    scope: ["identify"],
  });

  const response = await fetch("/activity/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, room: roomId }),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "Activity authentication failed.");
  }

  signalToken = data.signal_token;
  auth = await discordSdk.commands.authenticate({ access_token: data.access_token });
}

function authenticateCaptureWindow() {
  roomId = params.get("room") || "";
  signalToken = params.get("token") || "";
  if (!roomId || !signalToken) {
    throw new Error("Missing capture room. Open this window from the Discord Activity.");
  }

  roomLabel.textContent = `Room: ${roomId}`;
}

function createPeerConnection(remotePeerId) {
  knownPeers.add(remotePeerId);
  const existing = peers.get(remotePeerId);
  if (existing) return existing;

  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });

  pc.onicecandidate = (event) => {
    if (event.candidate) {
      send({ type: "candidate", to: remotePeerId, candidate: event.candidate });
    }
  };

  pc.ontrack = (event) => {
    const stream = event.streams[0];
    if (!stream) return;
    remoteStreams.set(remotePeerId, stream);
    renderRemoteTile(remotePeerId, stream);
  };

  if (localStream) {
    for (const track of localStream.getTracks()) {
      pc.addTrack(track, localStream);
    }
  }

  peers.set(remotePeerId, pc);
  return pc;
}

async function offerShareTo(remotePeerId) {
  const pc = createPeerConnection(remotePeerId);
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  send({ type: "offer", to: remotePeerId, description: pc.localDescription });
}

async function handleOffer(message) {
  const pc = createPeerConnection(message.from);
  await pc.setRemoteDescription(message.description);
  const answer = await pc.createAnswer();
  await pc.setLocalDescription(answer);
  send({ type: "answer", to: message.from, description: pc.localDescription });
}

async function handleAnswer(message) {
  const pc = peers.get(message.from);
  if (pc) {
    await pc.setRemoteDescription(message.description);
  }
}

async function handleCandidate(message) {
  const pc = peers.get(message.from);
  if (pc && message.candidate) {
    await pc.addIceCandidate(message.candidate);
  }
}

function renderRemoteTile(remotePeerId, stream) {
  if (!captureMode && !localVideo.srcObject) {
    showStreamInMainPanel(stream);
  }

  let tile = document.getElementById(`peer-${remotePeerId}`);
  if (!tile) {
    tile = document.createElement("article");
    tile.className = "tile";
    tile.id = `peer-${remotePeerId}`;
    tile.innerHTML = `<video autoplay muted playsinline></video><div class="tile-label">Remote screen</div>`;
    remoteGrid.appendChild(tile);
  }

  const video = tile.querySelector("video");
  video.muted = true;
  video.srcObject = stream;
  video.play().catch(() => {
    notice.textContent = "A remote screen connected, but Discord paused playback. Click inside the Activity and try again.";
  });
}

function removePeer(remotePeerId) {
  const pc = peers.get(remotePeerId);
  if (pc) pc.close();
  peers.delete(remotePeerId);
  knownPeers.delete(remotePeerId);
  remoteStreams.delete(remotePeerId);
  document.getElementById(`peer-${remotePeerId}`)?.remove();

  if (!captureMode && localVideo.srcObject) {
    const nextStream = remoteStreams.values().next().value;
    if (nextStream) {
      showStreamInMainPanel(nextStream);
    } else {
      refreshEmptyState();
    }
  }
}

function connectSignaling() {
  socket = new WebSocket(websocketUrl(roomId, signalToken));

  socket.addEventListener("open", () => {
    if (captureMode) {
      setStatus("Capture window connected");
    } else {
      setStatus(auth?.user?.username ? `Connected as ${auth.user.username}` : "Connected");
    }
    send({ type: "join" });
  });

  socket.addEventListener("message", async (event) => {
    const message = JSON.parse(event.data);
    if (message.from === peerId) return;

    if (message.type === "peers") {
      if (captureMode) {
        setStatus(`Capture window connected (${(message.peers || []).length} viewer${(message.peers || []).length === 1 ? "" : "s"})`);
      }
      for (const remotePeerId of message.peers || []) {
        knownPeers.add(remotePeerId);
        if (localStream) await offerShareTo(remotePeerId);
      }
    }
    if (message.type === "join") {
      knownPeers.add(message.from);
      if (localStream) await offerShareTo(message.from);
    }
    if (message.type === "offer") await handleOffer(message);
    if (message.type === "answer") await handleAnswer(message);
    if (message.type === "candidate") await handleCandidate(message);
    if (message.type === "share-started" && !captureMode) {
      setStatus("Receiving screen...");
    }
    if (message.type === "share-stopped" || message.type === "peer-left") removePeer(message.from);
  });

  socket.addEventListener("close", () => setStatus("Disconnected"));
}

async function startSharing() {
  localStream = await navigator.mediaDevices.getDisplayMedia({
    video: true,
    audio: true,
  });
  localVideo.srcObject = localStream;
  emptyState.style.display = "none";
  shareButton.disabled = true;
  stopButton.disabled = false;

  localStream.getVideoTracks()[0]?.addEventListener("ended", stopSharing);

  for (const remotePeerId of Array.from(knownPeers)) {
    removePeer(remotePeerId);
    knownPeers.add(remotePeerId);
    await offerShareTo(remotePeerId);
  }
  send({ type: "share-started" });
}

function stopSharing() {
  if (!localStream) return;
  for (const track of localStream.getTracks()) track.stop();
  localStream = null;
  localVideo.srcObject = null;
  refreshEmptyState();
  shareButton.disabled = false;
  stopButton.disabled = true;
  send({ type: "share-stopped" });
}

shareButton.addEventListener("click", async () => {
  try {
    if (captureMode) {
      await startSharing();
    } else {
      await openCaptureWindow();
    }
  } catch (error) {
    notice.textContent = `Could not start screen share: ${error.message}`;
  }
});
stopButton.addEventListener("click", stopSharing);

try {
  if (captureMode) {
    document.title = "Disco Capture Window";
    document.querySelector("h1").textContent = "Capture Window";
    emptyState.querySelector("strong").textContent = "Ready to share from this browser.";
    emptyState.querySelector("span").textContent = "Choose a screen or window here; viewers stay inside Discord.";
    shareButton.textContent = "Start Capture";
    setStatus("Connecting...");
    authenticateCaptureWindow();
  } else {
    document.querySelector("h1").textContent = "Screen Room";
    emptyState.querySelector("strong").textContent = "No screen is sharing.";
    emptyState.querySelector("span").textContent = "Open a capture window to share into this Activity.";
    shareButton.textContent = "Open Capture Window";
    stopButton.style.display = "none";
    setStatus("Authenticating...");
    await authenticateActivity();
  }
  notice.textContent = "";
  connectSignaling();
} catch (error) {
  setStatus("Setup failed");
  notice.textContent = error.message;
}
