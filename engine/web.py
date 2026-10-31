"""Conversational current-engine workbench. Run: python -m engine.web [port]."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from nlg import LEVELS
from .web_service import Workbench

ASSETS = Path(__file__).with_name("web_assets")
EXAMPLES = []  # Applications can supply examples for their configured domain.


def handler_for(workbench):
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body, content_type, filename=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if filename:
                self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json(self, body, code=200):
            self.send(code, json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            path = urlsplit(self.path).path
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                     "/sessions.js": ("sessions.js", "text/javascript"),
                     "/style.css": ("style.css", "text/css")}
            if path in files:
                name, mime = files[path]
                return self.send(200, (ASSETS / name).read_bytes(), mime + "; charset=utf-8")
            if path == "/api/status":
                return self.json({"engine": "current deterministic engine", "examples": EXAMPLES,
                                  "details": list(LEVELS), "policy": workbench.policy.model.describe(),
                                  "conversation": "Persistent sessions with explicit entity/metric/year follow-ups.",
                                  "journal": workbench.journal.path})
            try:
                params = parse_qs(urlsplit(self.path).query)
                if path == "/api/conversations":
                    return self.json(workbench.journal.conversations())
                if path == "/api/conversation":
                    return self.json(workbench.journal.conversation(params.get('id',[''])[0]))
                if path == '/api/export':
                    identity = params.get('id',[''])[0]
                    payload = workbench.journal.export(identity)
                    return self.send(200,json.dumps(payload,ensure_ascii=False).encode(),
                                     'application/json; charset=utf-8',f'resocor-conversation-{identity}.json')
                if path == "/api/logs":
                    offset = int(params.get('offset',['0'])[0])
                    if offset < 0:
                        raise ValueError('Invalid log offset.')
                    return self.json(workbench.journal.logs(offset))
                if path == "/api/turn":
                    return self.json(workbench.journal.get(params.get('id',[''])[0]))
                if path == "/api/source-manifest":
                    return self.json(workbench.journal.manifest(params.get('id',[''])[0]))
            except ValueError as exc:
                return self.json({'error':str(exc)},400)
            return self.json({"error": "Not found."}, 404)

        def do_POST(self):
            if self.path not in ("/api/ask", "/api/conversations", "/api/render"):
                return self.json({"error": "Not found."}, 404)
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                return self.json({"error": "Cross-origin requests are refused."}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("Request body must be 1 to 32768 bytes.")
                body = json.loads(self.rfile.read(length))
                if self.path == '/api/conversations':
                    result = workbench.journal.create()
                elif self.path == '/api/render':
                    result = workbench.render(body)
                else:
                    result = workbench.ask(body)
            except (ValueError, TypeError, UnicodeDecodeError) as exc:
                return self.json({"error": str(exc)}, 400)
            except Exception:
                self.log_error("Unexpected engine failure")
                return self.json({"error": "The engine could not complete this request. Try again."}, 500)
            return self.json(result)

    Handler.workbench_journal = workbench.journal
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", nargs="?", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(Workbench()))
    print(f"Resocor conversation: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.RequestHandlerClass.workbench_journal.close()


if __name__ == "__main__":
    main()
