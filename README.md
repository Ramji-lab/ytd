# ClipFetch — yt-dlp website

A simple Flask website for downloading a single YouTube video as video, MP3, a custom video+audio mix, or a ZIP containing each separate audio-only format exposed by yt-dlp.

**Files are temporary.** The server downloads and mixes the file in a temp folder (`DOWNLOAD_DIR`, default `/tmp/clipfetch-downloads`), then **deletes it as soon as your download completes**. Failed jobs are cleaned up immediately; files that are never downloaded (or whose download was interrupted) are removed after `FILE_TTL_MINUTES` (default 15). Leftovers from a crash are wiped at startup.

## Windows local setup

1. Install Python 3.10+ from https://www.python.org/downloads/ and enable **Add Python to PATH**.
2. Install FFmpeg and make sure `ffmpeg` is available in PATH. Windows builds: https://ffmpeg.org/download.html
3. Extract this project folder.
4. Double-click `run_windows.bat`.
5. Open http://127.0.0.1:5000

The batch file creates a virtual environment and installs Python packages automatically.

## Online deployment (Render)

1. Create a GitHub repository and upload the contents of this folder.
2. In Render, choose **New → Blueprint** and connect that repository, or create a Docker web service from it.
3. Render reads `render.yaml`; the Dockerfile installs FFmpeg and runs Gunicorn on `0.0.0.0:$PORT`.
4. Wait for deployment and open the service URL.

Temporary disk space is needed while a file is being mixed (roughly the size of the output). The download link works once; if a transfer is interrupted you can retry until the file expires.

## Custom mix

After checking a video, use **Custom mix** to pick a video quality (resolution, fps and codec), an audio track (language/bitrate) and an output container, then download them merged into one file.

- **MP4** is the most compatible; AAC audio is copied as-is and Opus audio is converted to AAC. **MKV** keeps both streams untouched.
- VP9/AV1 video inside MP4 plays on modern players but not on some older devices; choose an H.264 entry for maximum compatibility.

## Audio track behavior

- The **All audio tracks** option discovers distinct audio-only format IDs that yt-dlp exposes and downloads each into one ZIP.
- A YouTube video may expose only one audio stream, or may not expose every dubbed language as a separate format. The site cannot create tracks that are not made available by the source.
- Video merging and MP3 conversion require FFmpeg.
- The video option selects the best available video/audio combination; MP4 merging is attempted when possible.

## Important

Use this service only for videos you own or are authorized to download. Respect copyright, applicable law, and YouTube's terms. Do not deploy an unrestricted public instance: add authentication and abuse protections before sharing it publicly.


## Deploy on Netrun.io

1. Sign in to Netrun and choose **Create project**.
2. Upload this ZIP archive (or import the project from a GitHub repository).
3. Netrun detects the included `Dockerfile`. The image installs FFmpeg and Python dependencies, then starts the Flask app with Gunicorn on Netrun's `PORT`.
4. If Netrun asks for environment variables, add `MAX_ACTIVE_JOBS` with value `1`. Files are written to a temp folder and deleted after each download, so no persistent storage is required.
5. Publish the project, wait for the build to finish, then open the HTTPS address Netrun provides. Test `/api/health` and then try a video you own or are authorized to download.

This app currently has no user login; do not publish it widely until you add access control and abuse/rate limits.
