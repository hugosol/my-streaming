import argparse
import signal
import sys
from http.server import HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

import qrcode

from server.app import make_handler
from server.network import find_port, get_lan_ip
from server.scanner import scan_directory
from server.transcoder import Transcoder


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


TEMP_DIR = Path.cwd() / "temp"


def _print_qr(url):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make()
    qr.print_ascii(invert=True)


def main():
    parser = argparse.ArgumentParser(description="LAN video streaming server")
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory containing .mp4 files (default: current directory)",
    )
    args = parser.parse_args()

    dir_path = Path(args.directory).resolve()
    if not dir_path.is_dir():
        print(f"Error: '{args.directory}' is not a directory")
        sys.exit(1)

    videos = scan_directory(str(dir_path))

    ip = get_lan_ip()
    port = find_port(8888)

    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    transcoder = Transcoder(TEMP_DIR)
    transcoder.cleanup_expired()

    zc = None
    mdns_info = None
    try:
        from server.network import start_mdns, stop_mdns
        zc, mdns_info = start_mdns(ip, port)
        url = f"http://my-streaming.local:{port}/"
        print(f"mDNS: {url} (fallback: http://{ip}:{port}/)")
    except Exception as e:
        url = f"http://{ip}:{port}/"
        print(f"mDNS not available: {e}")
        print(f"Streaming server: {url}")

    def shutdown():
        print("\nShutting down...")
        if zc is not None:
            try:
                from server.network import stop_mdns
                stop_mdns(zc, mdns_info)
            except Exception:
                pass
        transcoder.stop_all()

    signal.signal(signal.SIGINT, lambda s, f: (shutdown(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda s, f: (shutdown(), sys.exit(0)))

    handler = make_handler(str(dir_path), transcoder, TEMP_DIR)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)

    print(f"Found {len(videos)} video(s):")
    for v in videos:
        sub = "CC" if v.has_subtitle else "  "
        print(f"  [{sub}] {v.name}")
    print()

    _print_qr(url)

    print()
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()


if __name__ == "__main__":
    main()
