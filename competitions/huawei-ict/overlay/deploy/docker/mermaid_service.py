#!/usr/bin/env python3
"""Render Mermaid in a restricted, networkless container over a Unix socket."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import subprocess
import uuid

SOCKET = Path('/run/zhiyan-mermaid/render.sock')
LIMIT = 128 * 1024


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log user diagrams.

    def do_GET(self):
        if self.path != '/health':
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')

    def do_POST(self):
        if self.path != '/render':
            self.send_error(404)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= LIMIT:
                raise ValueError('invalid request size')
            data = json.loads(self.rfile.read(size))
            source = data['source']
            width, height, scale = int(data['width']), int(data['height']), float(data['scale'])
            if not isinstance(source, str) or not source.strip() or len(source.encode()) > LIMIT:
                raise ValueError('invalid diagram')
            if not (100 <= width <= 3000 and 100 <= height <= 3000 and 0.5 <= scale <= 3):
                raise ValueError('invalid dimensions')
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            self.send_error(400)
            return
        token = uuid.uuid4().hex
        src, out = Path('/tmp') / (token + '.mmd'), Path('/tmp') / (token + '.png')
        try:
            src.write_text(source, encoding='utf-8')
            command = ['/opt/patent-tools/node_modules/.bin/mmdc', '-p', '/opt/zhiyan/mermaid-puppeteer.json',
                       '-i', str(src), '-o', str(out), '-b', 'white', '-w', str(width), '-H', str(height), '-s', str(scale)]
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180)
            if result.returncode or not out.is_file() or out.stat().st_size > 20 * 1024 * 1024:
                raise RuntimeError('render failed')
            image = out.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(image)))
            self.end_headers()
            self.wfile.write(image)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            self.send_error(503, 'Mermaid render unavailable')
        finally:
            src.unlink(missing_ok=True)
            out.unlink(missing_ok=True)


class UnixHTTPServer(socketserver.UnixStreamServer, HTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self):
        socketserver.UnixStreamServer.server_bind(self)
        self.server_name, self.server_port = 'localhost', 0


if SOCKET.exists():
    if not stat.S_ISSOCK(SOCKET.stat().st_mode):
        raise SystemExit('socket path is not a socket')
    SOCKET.unlink()
os.umask(0o007)
with UnixHTTPServer(str(SOCKET), Handler) as server:
    os.chmod(SOCKET, 0o660)
    server.serve_forever()
