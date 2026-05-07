# My Streaming

LAN video streaming server — point it at a directory of `.mp4` files and watch on any device in the same network.

## Quick Start

```bash
pip install -r requirements.txt
python server.py /path/to/videos
```

The server will print a URL and a QR code — scan it or open the URL on any device on the same LAN.

If no directory is given, it serves `.mp4` files from the current directory:

```bash
py server.py "E:\Developer\youtube-playground\download\yt-llm-translate"
```

## Features

- On-the-fly HLS transcoding (no conversion needed ahead of time)
- Resume playback from where you left off (per-video, stored in browser)
- Subtitles support (`.srt` files with the same name as the video)
- Rotate-to-fullscreen with custom fullscreen button
- QR code for easy mobile access

## Requirements

- Python 3.8+
- FFmpeg (must be on `PATH` for transcoding)
