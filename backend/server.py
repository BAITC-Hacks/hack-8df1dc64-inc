"""Local demo HTTP API. Run from repository root: python -m backend.server."""

import argparse
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.agent import AgentService
from backend.config import ROOT, load_env
from backend.openai_analysis import AnalysisError
from backend.service import ForecastService, strict_json
from backend.validation import ForecastValidationError

MAX_BODY = 1024 * 1024
DEFAULT_ORIGINS = ("http://127.0.0.1:3000", "http://localhost:3000")
ROUTES = {"/api/health": "GET", "/api/history/summary": "POST", "/api/forecasts": "POST",
          "/api/analysis/weather": "POST", "/api/agent/forecasts": "POST"}


class HTTPError(Exception):
    def __init__(self, status, code, field, message):
        self.status = status
        self.body = {"error": {"code": code, "field": field, "message": message}}


def create_server(address=("127.0.0.1", 8000), service=None, origins=DEFAULT_ORIGINS, analyzer=None):
    service = service if service is not None else ForecastService(weather_archive_dir=os.getenv("WEATHER_ARCHIVE_DIR") or ROOT / "data" / "weather")
    agent = AgentService(service, analyzer)
    allowed_origins = frozenset(origins)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def respond(self, status, body):
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Vary", "Origin")
            origin = self.headers.get("Origin")
            if origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(encoded)

        def body(self):
            if self.headers.get_content_type() != "application/json":
                raise HTTPError(415, "UNSUPPORTED_MEDIA_TYPE", "body", "Use application/json.")
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
                raise HTTPError(400, "INVALID_JSON", "body", "A single Content-Length is required; transfer encoding is unsupported.")
            significant_length = lengths[0].lstrip("0") or "0"
            if len(significant_length) > len(str(MAX_BODY)):
                raise HTTPError(413, "PAYLOAD_TOO_LARGE", "body", "Maximum request size is 1 MiB.")
            length = int(significant_length)
            if length > MAX_BODY:
                raise HTTPError(413, "PAYLOAD_TOO_LARGE", "body", "Maximum request size is 1 MiB.")
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete body")
                return strict_json(raw.decode("utf-8"))
            except (ValueError, RecursionError, TimeoutError) as exc:
                raise HTTPError(400, "INVALID_JSON", "body", "Invalid UTF-8 JSON, duplicate key, non-finite number or incomplete body.") from exc

        def dispatch(self):
            try:
                origin = self.headers.get("Origin")
                if origin is not None and origin not in allowed_origins:
                    raise HTTPError(403, "ORIGIN_NOT_ALLOWED", "Origin", "Origin is not allowed.")
                if self.path not in ROUTES:
                    raise HTTPError(404, "NOT_FOUND", "path", "Unknown API route.")
                if self.command == "OPTIONS":
                    self.respond(200, {})
                    return
                if self.command != ROUTES[self.path]:
                    raise HTTPError(405, "METHOD_NOT_ALLOWED", "method", "Method is not supported for this route.")
                if self.path == "/api/health":
                    result = {"status": "ok", "service": "wind-forecast-backend"}
                elif self.path == "/api/history/summary":
                    result = service.history_summary(self.body())
                elif self.path == "/api/analysis/weather":
                    result = agent.weather_analysis(self.body())
                elif self.path == "/api/agent/forecasts":
                    result = agent.forecast(self.body())
                else:
                    result = service.forecast(self.body())
                self.respond(200, result)
            except HTTPError as exc:
                self.respond(exc.status, exc.body)
            except ForecastValidationError as exc:
                status = 503 if exc.code in ("HISTORY_UNAVAILABLE", "WEATHER_UNAVAILABLE") else 422
                self.respond(status, {"error": exc.as_dict()})
            except AnalysisError as exc:
                self.respond(exc.status, {"error": exc.as_dict()})
            except (BrokenPipeError, ConnectionResetError):
                logging.info("Client disconnected")
            except Exception:
                logging.exception("Unexpected backend failure")
                self.respond(500, {"error": {"code": "INTERNAL_ERROR", "field": "server", "message": "Unexpected server error; see server log."}})

        do_GET = do_POST = do_OPTIONS = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_TRACE = do_CONNECT = dispatch

    return ThreadingHTTPServer(address, Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    load_env()
    origins = tuple(value.strip() for value in os.getenv("UI_ORIGINS", ",".join(DEFAULT_ORIGINS)).split(",") if value.strip())
    logging.basicConfig(level=logging.INFO)
    with create_server((args.host, args.port), origins=origins) as server:
        logging.info("Backend listening at http://%s:%s", *server.server_address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logging.info("Backend stopped")


if __name__ == "__main__":
    main()
