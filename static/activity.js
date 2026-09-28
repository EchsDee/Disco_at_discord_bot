import { DiscordSDK } from "https://esm.sh/@discord/embedded-app-sdk@1";

const config = window.ACTIVITY_CONFIG || {};
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

function send(payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ peerId, ...payload }));
}

function websocketUrl(room, token) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/activity/ws/${encodeURIComponent(room)}?token=${encodeURIComponent(token)}`;
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
  let tile = document.getElementById(`peer-${remotePeerId}`);
  if (!tile) {
    tile = document.createElement("article");
    tile.className = "tile";
    tile.id = `peer-${remotePeerId}`;
    tile.innerHTML = `<video autoplay playsinline></video><div class="tile-label">Remote screen</div>`;
    remoteGrid.appendChild(tile);
  }
  tile.querySelector("video").srcObject = stream;
}

function removePeer(remotePeerId) {
  const pc = peers.get(remotePeerId);
  if (pc) pc.close();
  peers.delete(remotePeerId);
  knownPeers.delete(remotePeerId);
  remoteStreams.delete(remotePeerId);
  document.getElementById(`peer-${remotePeerId}`)?.remove();
}

function connectSignaling() {
  socket = new WebSocket(websocketUrl(roomId, signalToken));

  socket.addEventListener("open", () => {
    setStatus(auth?.user?.username ? `Connected as ${auth.user.username}` : "Connected");
    send({ type: "join" });
  });

  socket.addEventListener("message", async (event) => {
    const message = JSON.parse(event.data);
    if (message.from === peerId) return;

    if (message.type === "peers") {
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
  emptyState.style.display = "";
  shareButton.disabled = false;
  stopButton.disabled = true;
  send({ type: "share-stopped" });
}

shareButton.addEventListener("click", async () => {
  try {
    await startSharing();
  } catch (error) {
    notice.textContent = `Could not start screen share: ${error.message}`;
  }
});
stopButton.addEventListener("click", stopSharing);

try {
  setStatus("Authenticating...");
  await authenticateActivity();
  notice.textContent = "";
  connectSignaling();
} catch (error) {
  setStatus("Setup failed");
  notice.textContent = error.message;
}
