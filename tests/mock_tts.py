#!/usr/bin/env python3
"""Mock 9Router TTS endpoint — returns a valid 3-second silent mp3 per request.
Usage: python3 mock_tts.py &
Then set NINEROUTER_URL=http://127.0.0.1:9999/v1
"""
import http.server, json, os, subprocess, sys, tempfile

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if "/audio/speech" in self.path:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                 "-t", "3", "-c:a", "libmp3lame", out],
                capture_output=True, timeout=10
            )
            data = open(out, "rb").read()
            os.unlink(out)
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if "/models" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "edge-tts/en-US-GuyNeural"}]}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[mock9r] {args[0]}", flush=True)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    print(f"Mock 9Router listening on http://127.0.0.1:{port}/v1", flush=True)
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()