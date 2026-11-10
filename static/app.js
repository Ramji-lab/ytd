const $ = id => document.getElementById(id);
let currentUrl = "";
let pollTimer = null;
let audioCount = 0;
let videoList = [], audioList = [];
const lockables = () => document.querySelectorAll(".option, #mixBtn");
function setBusy(busy) { lockables().forEach(b => b.disabled = busy); if (!busy) updateMix(); }
function formatSize(n) { return n >= 1073741824 ? (n/1073741824).toFixed(2)+" GB" : Math.round(n/1048576)+" MB"; }
function fillSelect(sel, items) {
  sel.innerHTML = "";
  items.forEach(it => { const o = document.createElement("option"); o.value = it.id; o.textContent = it.label; sel.appendChild(o); });
}
function updateMix() {
  const v = videoList.find(x => x.id === $("videoSelect").value);
  const a = audioList.find(x => x.id === $("audioSelect").value);
  $("mixBtn").disabled = !(v && a);
  const total = (v?.size || 0) + (a?.size || 0);
  $("mixEstimate").textContent = !(v && a)
    ? "This video does not expose separate video and audio streams."
    : total ? `Estimated download: ~${formatSize(total)}` : "";
}

function showError(message) {
  $("error").textContent = message;
  $("error").classList.remove("hidden");
}
function clearError() {
  $("error").textContent = "";
  $("error").classList.add("hidden");
}
function formatDuration(seconds) {
  if (!seconds) return "Duration unavailable";
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
  return h ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${m}:${String(s).padStart(2,"0")}`;
}
async function inspectVideo() {
  clearError();
  const url = $("url").value.trim();
  if (!url) return showError("Paste a YouTube video URL first.");
  $("inspectBtn").disabled = true;
  $("inspectBtn").textContent = "Checking…";
  $("videoCard").classList.add("hidden");
  $("progressCard").classList.add("hidden");
  try {
    const response = await fetch("/api/info", {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({url})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not read this video.");
    currentUrl = url;
    $("videoTitle").textContent = data.title;
    $("videoDetails").textContent = `${data.uploader || "YouTube"} · ${formatDuration(data.duration)}`;
    $("thumb").src = data.thumbnail || "";
    videoList = data.video_formats || [];
    audioList = data.audio_tracks || [];
    fillSelect($("videoSelect"), videoList);
    fillSelect($("audioSelect"), audioList);
    updateMix();
    audioCount = audioList.length;
    $("audioSummary").textContent = audioCount
      ? `${audioCount} separate audio format(s) detected · Download as one ZIP`
      : "No separate audio-only formats were exposed by this video";
    $("trackNote").textContent = audioCount
      ? "Track list: " + data.audio_tracks.map(t => t.label).join("  ·  ")
      : "Some videos expose only one combined audio stream. yt-dlp can only download tracks made available by the source.";
    document.querySelector('[data-mode="all_audio"]').disabled = audioCount === 0;
    document.querySelector('[data-mode="all_audio"]').style.opacity = audioCount === 0 ? ".45" : "1";
    $("videoCard").classList.remove("hidden");
    $("videoCard").scrollIntoView({behavior:"smooth",block:"nearest"});
  } catch (err) { showError(err.message); }
  finally { $("inspectBtn").disabled = false; $("inspectBtn").innerHTML = 'Check video <span>→</span>'; }
}
async function startDownload(mode, extra = {}) {
  clearError();
  if (!currentUrl) return;
  setBusy(true);
  $("progressCard").classList.remove("hidden");
  $("downloadBtn").classList.add("hidden");
  $("resetBtn").classList.add("hidden");
  $("progressTitle").textContent = mode === "custom" ? "Mixing video and audio" : mode === "all_audio" ? "Downloading all audio tracks" : mode === "mp3" ? "Preparing MP3" : "Downloading video";
  $("progressMessage").textContent = "Starting…";
  $("progressBar").style.width = "0%";
  $("progressPct").textContent = "0%";
  try {
    const response = await fetch("/api/download", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({url:currentUrl,mode,...extra})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not start download.");
    poll(data.job_id);
  } catch (err) {
    showError(err.message);
    setBusy(false);
  }
}
async function poll(jobId) {
  try {
    const response = await fetch(`/api/status/${jobId}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not read download status.");
    $("progressBar").style.width = `${data.progress || 0}%`;
    $("progressPct").textContent = `${data.progress || 0}%`;
    $("progressMessage").textContent = data.message || "Working…";
    if (data.status === "done") {
      $("progressTitle").textContent = "Your file is ready";
      $("progressMessage").textContent = "Click download. The file is deleted from the server as soon as your download completes.";
      $("downloadBtn").href = data.download_url;
      $("downloadBtn").classList.remove("hidden");
      $("resetBtn").classList.remove("hidden");
      setBusy(false);
      return;
    }
    if (data.status === "error") throw new Error(data.message || "Download failed.");
    pollTimer = setTimeout(() => poll(jobId), 1200);
  } catch (err) {
    showError(err.message);
    setBusy(false);
  }
}
$("downloadBtn").addEventListener("click", () => {
  // The server removes the file once the transfer completes, so the link works once.
  setTimeout(() => {
    $("downloadBtn").classList.add("hidden");
    $("progressTitle").textContent = "Download started";
    $("progressMessage").textContent = "Your browser is saving the file. It will be removed from the server when finished.";
  }, 300);
});
$("inspectBtn").addEventListener("click", inspectVideo);
$("videoSelect").addEventListener("change", updateMix);
$("audioSelect").addEventListener("change", updateMix);
$("mixBtn").addEventListener("click", () => startDownload("custom", {
  video_id: $("videoSelect").value, audio_id: $("audioSelect").value, container: $("containerSelect").value
}));
$("url").addEventListener("keydown", e => { if (e.key === "Enter") inspectVideo(); });
document.querySelectorAll(".option").forEach(button => button.addEventListener("click", () => startDownload(button.dataset.mode)));
$("resetBtn").addEventListener("click", () => {
  clearTimeout(pollTimer);
  $("progressCard").classList.add("hidden");
  $("videoCard").classList.add("hidden");
  $("url").value = "";
  $("url").focus();
});
