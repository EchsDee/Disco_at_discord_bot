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
let relayTimer = null;
let lastRelayFrameAt = 0;
let relayImageUrl = "";
let relayEncoding = false;
const RELAY_FRAME_INTERVAL_MS = 33;
const RELAY_MAX_WIDTH = 960;
const RELAY_JPEG_QUALITY = 0.52;
const RELAY_MAX_BUFFERED_BYTES = 800_000;

const statusPill = document.getElementById("statusPill");
const localVideo = document.getElementById("localVideo");
const relayImage = document.getElementById("relayImage");
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
  relayImage.style.display = "none";
  localVideo.muted = true;
  localVideo.style.display = "";
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
  localVideo.style.display = "";
  relayImage.removeAttribute("src");
  relayImage.style.display = "none";
  emptyState.style.display = "";
}

function showRelayFrame(blob) {
  if (!blob) return;
  lastRelayFrameAt = Date.now();
  localVideo.pause();
  localVideo.srcObject = null;
  localVideo.style.display = "none";
  if (relayImageUrl) {
    URL.revokeObjectURL(relayImageUrl);
  }
  relayImageUrl = URL.createObjectURL(blob);
  relayImage.src = relayImageUrl;
  relayImage.style.display = "block";
  emptyState.style.display = "none";
  setStatus("Receiving screen relay");
}

function sendBinary(payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  if (socket.bufferedAmount > RELAY_MAX_BUFFERED_BYTES) return;
  socket.send(payload);
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

function stopFrameRelay() {
  if (relayTimer) {
    clearInterval(relayTimer);
    relayTimer = null;
  }
}

function startFrameRelay() {
  stopFrameRelay();
  if (!captureMode || !localStream) return;

  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) return;

  relayTimer = setInterval(() => {
    if (relayEncoding || !localStream || socket?.readyState !== WebSocket.OPEN || localVideo.readyState < 2) {
      return;
    }

    const sourceWidth = localVideo.videoWidth || 1280;
    const sourceHeight = localVideo.videoHeight || 720;
    const maxWidth = RELAY_MAX_WIDTH;
    const scale = Math.min(1, maxWidth / sourceWidth);
    const width = Math.max(1, Math.round(sourceWidth * scale));
    const height = Math.max(1, Math.round(sourceHeight * scale));

    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    context.drawImage(localVideo, 0, 0, width, height);
    relayEncoding = true;
    canvas.toBlob((blob) => {
      relayEncoding = false;
      if (blob) {
        sendBinary(blob);
      }
    }, "image/jpeg", RELAY_JPEG_QUALITY);
  }, RELAY_FRAME_INTERVAL_MS);
}

function handleBinaryFrame(data) {
  const bytes = new Uint8Array(data);
  if (bytes.length < 2) return;

  const headerLength = (bytes[0] << 8) | bytes[1];
  if (bytes.length < 2 + headerLength) return;

  try {
    const headerText = new TextDecoder().decode(bytes.slice(2, 2 + headerLength));
    const header = JSON.parse(headerText);
    if (header.from === peerId || header.type !== "relay-frame") return;
  } catch {
    return;
  }

  const imageBytes = bytes.slice(2 + headerLength);
  showRelayFrame(new Blob([imageBytes], { type: "image/jpeg" }));
}

async function handleSocketMessage(event) {
  if (event.data instanceof Blob) {
    handleBinaryFrame(await event.data.arrayBuffer());
    return;
  }
  if (event.data instanceof ArrayBuffer) {
    handleBinaryFrame(event.data);
    return;
  }

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
  if (message.type === "relay-frame" && !captureMode) {
    const response = await fetch(message.image);
    showRelayFrame(await response.blob());
  }
  if (message.type === "share-stopped" || message.type === "peer-left") removePeer(message.from);
}

function connectSignaling() {
  socket = new WebSocket(websocketUrl(roomId, signalToken));
  socket.binaryType = "arraybuffer";

  socket.addEventListener("open", () => {
    if (captureMode) {
      setStatus("Capture window connected");
    } else {
      setStatus(auth?.user?.username ? `Connected as ${auth.user.username}` : "Connected");
    }
    send({ type: "join" });
  });

  socket.addEventListener("message", (event) => {
    handleSocketMessage(event).catch((error) => {
      notice.textContent = `Activity connection error: ${error.message}`;
    });
  });

  socket.addEventListener("close", () => setStatus("Disconnected"));
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

  if (!captureMode) {
    const nextStream = remoteStreams.values().next().value;
    if (nextStream) {
      showStreamInMainPanel(nextStream);
    } else {
      refreshEmptyState();
    }
  }
}

async function startSharing() {
  localStream = await navigator.mediaDevices.getDisplayMedia({
    video: true,
    audio: true,
  });
  localVideo.srcObject = localStream;
  localVideo.style.display = "";
  relayImage.style.display = "none";
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
  startFrameRelay();
}

function stopSharing() {
  if (!localStream) return;
  for (const track of localStream.getTracks()) track.stop();
  stopFrameRelay();
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
