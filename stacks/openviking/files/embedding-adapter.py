#!/usr/bin/env python3
"""Internal, single-input OpenAI embeddings bridge to two fixed Ollama runtimes."""
import http.client
import json
import math
import socket
import threading
from typing import cast
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "qwen3-embedding:0.6b"
PREFIX = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
# Source-managed destinations only. Never read upstream addresses from a request or environment.
UPSTREAMS = {"pc": ("192.168.90.212", 11434, 30), "cpu": ("embeddings", 11434, 180)}
CONNECT_TIMEOUT = 2
BODY_LIMIT = 65536
RESPONSE_LIMIT = 131072
DIMENSION = 1024


class Rejected(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def payload(body):
    if not isinstance(body, dict) or set(body) - {
        "model", "input", "input_type", "encoding_format", "dimensions"
    }:
        raise Rejected(400, "invalid_request", "Unsupported embedding request fields")
    if body.get("model") != MODEL:
        raise Rejected(400, "unknown_model", "Unsupported model")
    text = body.get("input")
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise Rejected(400, "invalid_input", "input must be one nonempty text string")
    if body.get("encoding_format") != "float":
        raise Rejected(400, "invalid_encoding", "encoding_format must be float")
    if type(body.get("dimensions", DIMENSION)) is not int or body.get("dimensions", DIMENSION) != DIMENSION:
        raise Rejected(400, "invalid_dimensions", "Only 1024 dimensions are supported")
    if body.get("input_type") not in ("query", "document"):
        raise Rejected(400, "invalid_input_type", "input_type must be query or document")
    if body["input_type"] == "query":
        if text.startswith(PREFIX):
            raise Rejected(400, "prefixed_query", "Send plain query text without an instruction prefix")
        text = PREFIX + text
    return {"model": MODEL, "input": text, "truncate": False,
            "keep_alive": -1, "options": {"num_ctx": 4096, "num_thread": 2}}


def embed(route, request):
    host, port, read_timeout = UPSTREAMS[route]
    connection = http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT)
    try:
        connection.connect()
        connection.sock.settimeout(read_timeout)
        connection.request("POST", "/api/embed", json.dumps(request).encode(),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(RESPONSE_LIMIT + 1)
        if len(raw) > RESPONSE_LIMIT:
            raise Rejected(502, "invalid_upstream", "Upstream response exceeds limit")
        if response.status != 200:
            # Never echo upstream bodies. They may contain document text.
            if response.status == 400:
                try:
                    error = json.loads(raw).get("error", "").lower()
                except (ValueError, AttributeError):
                    error = ""
                if "context length" in error or "input length" in error:
                    raise Rejected(400, "input_too_large", "Input exceeds the 4096-token model context")
            if response.status in (408, 429) or response.status >= 500:
                raise Rejected(503, "upstream_unavailable", "Embedding runtime unavailable or busy")
            # A missing model/auth misconfiguration is an endpoint problem. Let OV try the peer.
            raise Rejected(502, "upstream_configuration", "Embedding runtime rejected the request")
        data = json.loads(raw)
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != 1 or not isinstance(vectors[0], list):
            raise ValueError("embedding count")
        vector = vectors[0]
        if len(vector) != DIMENSION or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
            raise ValueError("embedding values")
        norm = math.sqrt(sum(v * v for v in vector))
        if not math.isfinite(norm) or abs(norm - 1) > 0.01:
            raise ValueError("embedding norm")
        vector = [value / norm for value in vector]
        tokens = data.get("prompt_eval_count")
        if type(tokens) is not int or tokens < 1 or tokens > 4096:
            raise ValueError("usage")
        return {"object": "list", "model": MODEL,
                "data": [{"object": "embedding", "index": 0, "embedding": vector}],
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens}}
    except (socket.timeout, TimeoutError):
        raise Rejected(504, "upstream_timeout", "Embedding runtime timed out") from None
    except (OSError, http.client.HTTPException):
        raise Rejected(503, "upstream_unavailable", "Embedding runtime unavailable") from None
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise Rejected(502, "invalid_upstream", "Invalid embedding response") from None
    finally:
        connection.close()


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 closes each socket, bounding idle clients without a connection pool.
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        pass

    def send_json(self, status, body):
        raw = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, {"error": {"code": "not_found", "message": "Unknown endpoint"}})

    def do_POST(self):
        acquired = False
        try:
            routes = {"/" + key + "/v1/embeddings": key for key in UPSTREAMS}
            if self.path not in routes:
                raise Rejected(404, "not_found", "Unknown endpoint")
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                raise Rejected(400, "invalid_length", "One Content-Length is required")
            try:
                length = int(self.headers["Content-Length"])
            except ValueError:
                raise Rejected(400, "invalid_length", "Invalid Content-Length") from None
            if length <= 0 or length > BODY_LIMIT:
                raise Rejected(413, "body_too_large", "Request exceeds body limit")
            if self.headers.get_content_type() != "application/json":
                raise Rejected(415, "invalid_content_type", "Use application/json")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise Rejected(400, "incomplete_body", "Incomplete request body")
            try:
                body = json.loads(raw, object_pairs_hook=strict_object,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                request = payload(body)
            except (ValueError, UnicodeError):
                raise Rejected(400, "invalid_json", "Invalid JSON body") from None
            acquired = cast("Server", self.server).inference.acquire(blocking=False)
            if not acquired:
                raise Rejected(429, "busy", "One embedding request may run at a time")
            self.send_json(200, embed(routes[self.path], request))
        except Rejected as error:
            self.send_json(error.status, {"error": {"code": error.code, "message": error.message,
                                                  "type": "invalid_request_error" if error.status < 500 else "server_error"}})
        except (OSError, TimeoutError):
            self.send_json(408, {"error": {"code": "body_timeout", "message": "Request body timed out"}})
        finally:
            if acquired:
                cast("Server", self.server).inference.release()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address):
        self.workers = threading.BoundedSemaphore(8)
        self.inference = threading.BoundedSemaphore(1)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.workers.release()

    def handle_error(self, request, client_address):
        # No request bodies, headers, credentials or exception text in logs.
        pass


if __name__ == "__main__":
    Server(("0.0.0.0", 8000)).serve_forever()
