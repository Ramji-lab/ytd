import os
import re
import time
import uuid
import shutil
import zipfile
import threading
from pathlib import Path
from urllib.parse import urlparse, quote

from flask import Flask, jsonify, render_template, request, abort, Response
import yt_dlp

# Files are mixed in a temporary folder on the server and deleted as soon as the
# download completes. Unfetched files are removed after FILE_TTL_MINUTES.
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_ROOT = Path(os.environ.get("DOWNLOAD_DIR", "/tmp/clipfetch-downloads"))
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
FILE_TTL = int(os.environ.get("FILE_TTL_MINUTES", "15")) * 60
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

jobs = {}
jobs_lock = threading.Lock()
MAX_ACTIVE_JOBS = int(os.environ.get("MAX_ACTIVE_JOBS", "2"))
MAX_AUDIO_FORMATS = int(os.environ.get("MAX_AUDIO_FORMATS", "30"))
MAX_VIDEO_FORMATS = int(os.environ.get("MAX_VIDEO_FORMATS", "40"))
FORMAT_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,40}$")
MIME = {".mp4": "video/mp4", ".mkv": "video/x-matroska", ".mp3": "audio/mpeg", ".zip": "application/zip"}

def valid_youtube_url(raw):
    try:
        parsed = urlparse(raw.strip())
        host = (parsed.hostname or "").lower()
        allowed = host in {
            "youtube.com", "www.youtube.com", "m.youtube.com",
            "music.youtube.com", "youtu.be", "www.youtube-nocookie.com"
        }
        return parsed.scheme in ("http", "https") and allowed
    except Exception:
        return False

def safe_title(value):
    value = re.sub(r'[\\/:*?"<>|\r\n]+', "_", value or "download")
    return value[:100].strip(" .") or "download"

def extract_info(url, fmt=None):
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "noplaylist": True, "extract_flat": False, "socket_timeout": 20,
    }
    if fmt:
        opts["format"] = fmt
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def _has(codec):
    return codec not in (None, "none", "")

def _size(f):
    return f.get("filesize") or f.get("filesize_approx") or 0

def _codec_family(vcodec):
    v = (vcodec or "").lower()
    if v.startswith(("avc1", "h264")):
        return "H.264"
    if v.startswith("vp09") or v.startswith("vp9"):
        return "VP9"
    if v.startswith("av01"):
        return "AV1"
    if v.startswith("hev") or v.startswith("hvc"):
        return "H.265"
    return v.split(".")[0].upper() or "video"

def _fmt_size(n):
    if not n:
        return ""
    return f"{n / 1048576:.0f} MB" if n >= 1048576 else f"{n / 1024:.0f} KB"

def audio_formats(info):
    found, seen = [], set()
    for f in info.get("formats") or []:
        if not _has(f.get("acodec")) or _has(f.get("vcodec")):
            continue
        fid = str(f.get("format_id") or "")
        if not fid or fid in seen:
            continue
        seen.add(fid)
        lang = f.get("language") or ""
        note = (f.get("format_note") or "").strip()
        name = " · ".join(x for x in (lang, note) if x) or "Audio track"
        ext = f.get("ext") or "audio"
        abr = f.get("abr")
        label = f"{name} · {ext.upper()}" + (f" · {int(abr)} kbps" if abr else "")
        found.append({
            "id": fid, "label": label, "language": lang or note or "Audio track",
            "ext": ext, "acodec": f.get("acodec"), "abr": abr or 0, "size": _size(f),
        })
    # group by language, best bitrate first inside each group
    found.sort(key=lambda t: (t["language"].lower(), -t["abr"]))
    return found[:MAX_AUDIO_FORMATS]

def video_formats(info):
    """Video-only streams, one entry per (height, fps, codec family), best bitrate kept."""
    best = {}
    for f in info.get("formats") or []:
        if not _has(f.get("vcodec")) or _has(f.get("acodec")):
            continue
        fid = str(f.get("format_id") or "")
        height = f.get("height") or 0
        if not fid or not height:
            continue
        fps = int(f.get("fps") or 0)
        family = _codec_family(f.get("vcodec"))
        key = (height, fps, family)
        score = f.get("tbr") or 0
        if key not in best or score > best[key][0]:
            best[key] = (score, f, family, fps)
    items = []
    for (height, fps, family), (_, f, _, _) in best.items():
        ext = f.get("ext") or "video"
        label = f"{height}p" + (f"{fps}" if fps > 30 else "") + f" · {family} · {ext.upper()}"
        sz = _fmt_size(_size(f))
        if sz:
            label += f" · ~{sz}"
        items.append({"id": str(f["format_id"]), "label": label, "height": height,
                      "fps": fps, "codec": family, "ext": ext, "size": _size(f)})
    items.sort(key=lambda v: (-v["height"], -v["fps"], v["codec"]))
    return items[:MAX_VIDEO_FORMATS]

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify({"ok": True, "yt_dlp": yt_dlp.version.__version__,
                    "ffmpeg": bool(shutil.which("ffmpeg"))})

@app.post("/api/info")
def api_info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not valid_youtube_url(url):
        return jsonify({"error": "Please enter a valid YouTube video URL."}), 400
    try:
        info = extract_info(url)
        if info.get("_type") in ("playlist", "multi_video"):
            return jsonify({"error": "Please use a single video URL, not a playlist."}), 400
        return jsonify({
            "title": info.get("title") or "YouTube video",
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
            "audio_tracks": audio_formats(info),
            "video_formats": video_formats(info),
            "has_video": any(_has(f.get("vcodec")) for f in info.get("formats") or []),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)[:500]}), 400

# ---------- jobs ----------

def set_job(job_id, **updates):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(updates, updated=time.time())

def cleanup_job(job_id, keep_status=False):
    """Delete the job's files from disk (and its record unless keep_status)."""
    shutil.rmtree(DOWNLOAD_ROOT / job_id, ignore_errors=True)
    if not keep_status:
        with jobs_lock:
            jobs.pop(job_id, None)

def progress_hook(job_id, data):
    status = data.get("status")
    with jobs_lock:
        job = jobs.get(job_id) or {}
        parts = job.get("parts", 1)
        finished = job.get("finished_parts", 0)
    if status == "downloading":
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        pct = (data.get("downloaded_bytes", 0) / total * 100) if total else 0
        label = f"Downloading stream {min(finished + 1, parts)} of {parts}…" if parts > 1 else "Downloading…"
        set_job(job_id, progress=max(5, min(95, int((finished * 100 + pct) / parts))), message=label)
    elif status == "finished":
        set_job(job_id, finished_parts=finished + 1)
        if finished + 1 >= parts:
            set_job(job_id, progress=96, message="Processing downloaded file…")

def pp_hook(job_id, data):
    if data.get("status") == "started":
        msg = "Mixing video and audio with FFmpeg…" if data.get("postprocessor") == "Merger" else "Processing with FFmpeg…"
        set_job(job_id, progress=97, message=msg)

def run_job(job_id, url, mode, options=None):
    options = options or {}
    folder = DOWNLOAD_ROOT / job_id
    folder.mkdir(parents=True, exist_ok=True)
    set_job(job_id, status="running", progress=5, message="Connecting to YouTube…")
    try:
        common = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "outtmpl": str(folder / "%(title).80s [%(format_id)s].%(ext)s"),
            "progress_hooks": [lambda d: progress_hook(job_id, d)],
            "postprocessor_hooks": [lambda d: pp_hook(job_id, d)],
            "socket_timeout": 30, "retries": 2, "overwrites": True,
        }
        if mode in ("video", "custom"):
            if mode == "custom":
                fmt, container = f"{options['video_id']}+{options['audio_id']}", options["container"]
            else:
                fmt, container = "bestvideo*+bestaudio/best", "mp4"
            info = extract_info(url, fmt)  # also validates the chosen format IDs
            chosen = info.get("requested_formats") or [info]
            height = max((f.get("height") or 0) for f in chosen)
            opts = dict(common)
            opts.update({
                "format": fmt, "merge_output_format": container,
                "outtmpl": str(folder / ("%(title).80s" + (f" [{height}p]" if height else "") + ".%(ext)s")),
            })
            # MP4 only holds AAC reliably: copy AAC as-is, convert Opus/Vorbis to AAC.
            afmt = next((f for f in chosen if _has(f.get("acodec"))), None)
            if container == "mp4" and afmt and not str(afmt.get("acodec")).startswith("mp4a"):
                opts["postprocessor_args"] = {"merger+ffmpeg_o": ["-c:a", "aac", "-b:a", "192k"]}
            set_job(job_id, parts=len(chosen), message="Downloading selected video and audio…")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        elif mode == "mp3":
            opts = dict(common)
            opts.update({
                "format": "bestaudio/best",
                "outtmpl": str(folder / "%(title).80s.%(ext)s"),
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
            })
            set_job(job_id, message="Extracting audio as MP3…")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        elif mode == "all_audio":
            info = extract_info(url)
            title = safe_title(info.get("title"))
            tracks = audio_formats(info)
            if not tracks:
                raise RuntimeError("No separate audio tracks were exposed for this video.")
            for index, track in enumerate(tracks, 1):
                set_job(job_id, progress=8 + int((index - 1) / len(tracks) * 82),
                        message=f"Downloading audio track {index} of {len(tracks)}: {track['label']}")
                opts = dict(common)
                opts["format"] = track["id"]
                opts["progress_hooks"] = []
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
            set_job(job_id, message="Packing audio tracks into a ZIP file…", progress=93)
            archive = folder / f"{title} - all audio tracks.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in folder.iterdir():
                    if item.is_file() and item != archive:
                        zf.write(item, item.name)
            for item in list(folder.iterdir()):
                if item.is_file() and item != archive:
                    item.unlink(missing_ok=True)
        else:
            raise RuntimeError("Unknown download type.")

        outputs = [p for p in folder.iterdir() if p.is_file()]
        if not outputs:
            raise RuntimeError("The download finished but no output file was created. Check whether FFmpeg is installed.")
        out = max(outputs, key=lambda p: p.stat().st_size)
        set_job(job_id, status="done", progress=100, message="Your file is ready.",
                filename=out.name, size=out.stat().st_size)
    except Exception as exc:
        cleanup_job(job_id, keep_status=True)  # never leave partial files behind
        set_job(job_id, status="error", progress=0, message=str(exc)[:700])

@app.post("/api/download")
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    mode = data.get("mode")
    if not valid_youtube_url(url):
        return jsonify({"error": "Please enter a valid YouTube video URL."}), 400
    if mode not in ("video", "mp3", "all_audio", "custom"):
        return jsonify({"error": "Select a valid download option."}), 400
    options = {}
    if mode == "custom":
        options = {
            "video_id": str(data.get("video_id") or ""),
            "audio_id": str(data.get("audio_id") or ""),
            "container": data.get("container") or "mp4",
        }
        if not (FORMAT_ID_RE.match(options["video_id"]) and FORMAT_ID_RE.match(options["audio_id"])):
            return jsonify({"error": "Choose a video quality and an audio track."}), 400
        if options["container"] not in ("mp4", "mkv"):
            return jsonify({"error": "Choose MP4 or MKV as the output format."}), 400
    with jobs_lock:
        active = sum(1 for j in jobs.values() if j["status"] in ("queued", "running"))
        if active >= MAX_ACTIVE_JOBS:
            return jsonify({"error": "The downloader is busy. Please try again in a minute."}), 429
        job_id = uuid.uuid4().hex
        now = time.time()
        jobs[job_id] = {"status": "queued", "progress": 0, "message": "Queued…",
                        "filename": None, "size": 0, "created": now, "updated": now}
    threading.Thread(target=run_job, args=(job_id, url, mode, options), daemon=True).start()
    return jsonify({"job_id": job_id})

@app.get("/api/status/<job_id>")
def api_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "This download is no longer available (it was already downloaded or expired)."}), 404
        result = {k: v for k, v in job.items() if k not in ("created", "updated", "serving", "parts", "finished_parts")}
    if result.get("status") == "done":
        result["download_url"] = f"/api/file/{job_id}"
    return jsonify(result)

@app.get("/api/file/<job_id>")
def api_file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job.get("status") != "done" or not job.get("filename") or job.get("serving"):
            abort(404)
        job["serving"] = True
        name = job["filename"]
    path = DOWNLOAD_ROOT / job_id / name
    if not path.is_file():
        cleanup_job(job_id)
        abort(404)
    size = path.stat().st_size

    def generate():
        completed = False
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
            completed = True
        finally:
            if completed:
                cleanup_job(job_id)          # download finished -> delete from server
            else:
                set_job(job_id, serving=False)  # interrupted: allow a retry until it expires

    ascii_name = name.encode("ascii", "ignore").decode().replace('"', "") or "download"
    return Response(generate(), mimetype=MIME.get(path.suffix.lower(), "application/octet-stream"), headers={
        "Content-Length": str(size),
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "no-store",
    })

# ---------- housekeeping ----------

def wipe_leftovers():
    """Remove job folders left behind by a previous run/crash."""
    for p in DOWNLOAD_ROOT.iterdir():
        if p.is_dir() and JOB_ID_RE.match(p.name):
            shutil.rmtree(p, ignore_errors=True)

def reaper():
    while True:
        time.sleep(60)
        now = time.time()
        with jobs_lock:
            snapshot = list(jobs.items())
        for job_id, job in snapshot:
            idle = now - job.get("updated", job["created"])
            if job.get("serving") and idle < FILE_TTL * 4:
                continue
            if job["status"] in ("done", "error") and idle > FILE_TTL:
                cleanup_job(job_id)
            elif job["status"] in ("queued", "running") and now - job["created"] > 2 * 3600:
                cleanup_job(job_id)

wipe_leftovers()
threading.Thread(target=reaper, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
