#!/usr/bin/env python3
"""Internal bounded embedding and chat transports to source-managed Ollama runtimes."""
import hashlib
from pathlib import Path
import http.client
import json
import math
import socket
import threading
from typing import Any, cast
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "qwen3-embedding:0.6b"
PREFIX = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
# Source-managed destinations only. Never read upstream addresses from a request or environment.
# Embeddings use only the Normandy CPU runtime. The PC's Ollama 0.40.5 applies its
# global OLLAMA_KV_CACHE_TYPE=q4_0 to the embedding runner, with no per-request or
# per-model override, and its live vectors measured cosine 0.877 against CPU ones.
# The PC route answers with an explicit error so a stale configuration can never
# write or search with vectors that do not match the index.
UPSTREAMS = {"cpu": ("embeddings", 11434, 180)}
DISABLED_EMBEDDING_ROUTES = {"/pc/v1/embeddings"}
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


# Generation stays in this process and never accepts an upstream URL.
GENERATION_UPSTREAMS = {
    "pc": ("192.168.90.212", 11434, 120),
    "cpu": ("generation", 11434, 480),
}
GENERATION_MODELS = {"pc": "local:latest", "cpu": "ov-gen-4k"}
CPU_CONTEXT = 4096
OUTPUT_LIMIT = 512
CPU_TOKEN_MARGIN = 64
TOKENIZER_REVISION = "ba1cf1846d7df0a0591d6c00649f57e798519da8"
TOKENIZER_ASSETS = {
    "tokenizer.json": "f7c9b2dba4a296b1aa76c16a34b8225c0c118978400d4bb66bff0902d702f5b8",
    "tokenizer_config.json": "482bd979881423375ca5414e4e0d94cd7c5349dbb17fffd46b4d36d71e62a1bc",
    "ollama-template.txt": "62fbfd9ed093d6e5ac83190c86eec5369317919f4b149598d2dbb38900e9faef",
}


class PromptGuard:
    """Count only the verified legacy Ollama Qwen2 chat rendering, without truncation."""

    def __init__(self, directory):
        from tokenizers import Tokenizer, __version__

        if __version__ != "0.22.2":
            raise ValueError("CPU guard requires qualified tokenizers 0.22.2")
        for name, digest in TOKENIZER_ASSETS.items():
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
                raise ValueError("CPU tokenizer asset checksum mismatch: " + name)
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        # llama.cpp Qwen2 tokenizes the original UTF-8 bytes. HF ships an NFC
        # normalizer, which undercounts decomposed accents. Disable it for
        # counting; never normalize or rewrite the caller\'s actual messages.
        self.tokenizer.normalizer = None
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()

    def render(self, messages):
        # The pinned Ollama model has no system/default message or BOS. The HF
        # default template inserts a system message and must NOT be used here.
        # Restrict to an optional system and alternating user/assistant turns,
        # ending in user. This avoids legacy-template collation/continuation cases.
        rendered = ""
        expected = "user"
        for index, message in enumerate(messages):
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise Rejected(400, "unsupported_cpu_prompt", "CPU supports plain text role/content messages only")
            role, text = message["role"], message["content"]
            if not isinstance(text, str) or not text.strip() or "\x00" in text:
                raise Rejected(400, "unsupported_cpu_prompt", "CPU messages must contain nonempty text")
            text.encode("utf-8", errors="strict")
            if any(token in text for token in ("<|im_start|>", "<|im_end|>", "<|endoftext|>")):
                raise Rejected(400, "unsupported_cpu_prompt", "CPU text must not contain model control tokens")
            if role == "system" and index == 0:
                pass
            elif role == expected:
                expected = "assistant" if role == "user" else "user"
            else:
                raise Rejected(400, "unsupported_cpu_prompt", "CPU requires alternating user/assistant turns after optional system")
            rendered += "<|im_start|>" + role + "\n" + text + "<|im_end|>\n"
        if expected != "assistant":
            raise Rejected(400, "unsupported_cpu_prompt", "CPU prompt must end with a user message")
        return rendered + "<|im_start|>assistant\n"

    def count(self, messages):
        return len(self.tokenizer.encode(self.render(messages), add_special_tokens=False).ids)

    def check(self, messages):
        count = self.count(messages)
        # Reserve the FULL configured output even for a caller requesting less.
        if count + OUTPUT_LIMIT + CPU_TOKEN_MARGIN > CPU_CONTEXT:
            raise Rejected(413, "context_length_exceeded",
                           f"CPU prompt has {count} counted tokens; limit is "
                           f"{CPU_CONTEXT - OUTPUT_LIMIT - CPU_TOKEN_MARGIN}, reserving "
                           f"{OUTPUT_LIMIT} output tokens and {CPU_TOKEN_MARGIN} margin; split the input explicitly")
        return count


def generation_payload(route, body, guard):
    allowed = {"model", "messages", "max_tokens", "temperature", "stream",
               "reasoning_effort", "presence_penalty", "tools", "tool_choice", "response_format"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise Rejected(400, "invalid_request", "Unsupported chat request fields")
    if body.get("model") != GENERATION_MODELS[route]:
        raise Rejected(400, "unknown_model", "Unsupported route model")
    if body.get("stream", False) is not False:
        raise Rejected(400, "unsupported_stream", "OV transport supports nonstreaming completions only")
    maximum = body.get("max_tokens", OUTPUT_LIMIT)
    if type(maximum) is not int or not 1 <= maximum <= OUTPUT_LIMIT:
        raise Rejected(400, "invalid_output_budget", "max_tokens must be an integer from 1 to 512")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 128:
        raise Rejected(400, "invalid_messages", "Send 1 to 128 chat messages")
    if body.get("temperature", 0) != 0 or body.get("presence_penalty", 0) != 0:
        raise Rejected(400, "invalid_sampling", "This transport requires temperature=0 and presence_penalty=0")
    request: dict[str, Any] = dict(body, max_tokens=maximum, temperature=0, presence_penalty=0, stream=False)
    if route == "cpu":
        if set(body) & {"tools", "tool_choice", "response_format", "reasoning_effort"}:
            raise Rejected(400, "unsupported_cpu_prompt", "CPU tools, multimodal, reasoning and structured formats are unqualified")
        guard.check(messages)
    else:
        # Only inline image data, never remote image URLs. All HTTP destinations
        # remain the fixed PC endpoint even for tool or multimodal requests.
        for message in messages:
            if not isinstance(message, dict):
                raise Rejected(400, "invalid_messages", "Invalid chat message")
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        raise Rejected(400, "invalid_messages", "Invalid content part")
                    if item.get("type") == "image_url":
                        image = item.get("image_url")
                        if not isinstance(image, dict) or not isinstance(image.get("url"), str) or not image["url"].startswith("data:image/"):
                            raise Rejected(400, "invalid_image", "Only inline image data is supported")
                    elif item.get("type") != "text" or not isinstance(item.get("text"), str):
                        raise Rejected(400, "invalid_messages", "Unsupported content part")
        if body.get("reasoning_effort", "none") != "none":
            raise Rejected(400, "invalid_reasoning", "PC thinking is disabled")
        request["reasoning_effort"] = "none"
    return request


def generate(route, request):
    host, port, read_timeout = GENERATION_UPSTREAMS[route]
    connection = http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT)
    try:
        connection.connect()
        connection.sock.settimeout(read_timeout)
        connection.request("POST", "/v1/chat/completions", json.dumps(request).encode(),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(RESPONSE_LIMIT + 1)
        if len(raw) > RESPONSE_LIMIT:
            raise Rejected(502, "invalid_upstream", "Chat response exceeds limit")
        if response.status != 200:
            # Classify, never echo upstream request or credential content.
            try:
                error = json.loads(raw).get("error", "")
                message = error.get("message", "") if isinstance(error, dict) else error
                message = message.lower() if isinstance(message, str) else ""
            except (ValueError, AttributeError):
                message = ""
            if response.status == 413 or response.status == 400 and any(
                word in message for word in ("context", "input length", "too long", "too large")
            ):
                raise Rejected(413, "context_length_exceeded", "Chat runtime rejected the prompt context")
            if response.status in (400, 422):
                raise Rejected(400, "invalid_chat_request", "Chat runtime rejected the request format")
            if response.status in (408, 429) or response.status >= 500:
                raise Rejected(503, "upstream_unavailable", "Chat runtime unavailable or busy")
            raise Rejected(502, "upstream_configuration", "Chat endpoint configuration failure")
        data = json.loads(raw)
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("choices")
        choice = choices[0]
        message = choice["message"]
        if message.get("role") != "assistant" or choice.get("finish_reason") not in ("stop", "length", "tool_calls"):
            raise ValueError("completion")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            if route != "pc" or choice.get("finish_reason") != "tool_calls" or not message.get("tool_calls"):
                raise ValueError("empty completion")
        usage = data["usage"]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if type(usage.get(key)) is not int or usage[key] < 0:
                raise ValueError("usage")
        if usage["prompt_tokens"] < 1 or usage["completion_tokens"] > request["max_tokens"]:
            raise ValueError("usage bounds")
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            raise ValueError("usage sum")
        if route == "cpu" and usage["prompt_tokens"] + OUTPUT_LIMIT > CPU_CONTEXT:
            raise Rejected(413, "context_length_exceeded", "CPU runtime reported an unsafe context")
        return data
    except (socket.timeout, TimeoutError):
        raise Rejected(504, "upstream_timeout", "Chat runtime timed out") from None
    except (OSError, http.client.HTTPException):
        raise Rejected(503, "upstream_unavailable", "Chat runtime unavailable") from None
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, OverflowError):
        raise Rejected(502, "invalid_upstream", "Invalid or empty chat response") from None
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
        permit = None
        try:
            routes = {"/" + key + "/v1/embeddings": key for key in UPSTREAMS}
            chat_routes = {"/" + key + "/v1/chat/completions": key for key in GENERATION_UPSTREAMS}
            if self.path in DISABLED_EMBEDDING_ROUTES:
                raise Rejected(410, "embedding_route_disabled",
                               "PC embeddings are disabled because they do not match the CPU index; "
                               "use /cpu/v1/embeddings")
            if self.path not in routes and self.path not in chat_routes:
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
                request = (generation_payload(chat_routes[self.path], body, cast("Server", self.server).guard)
                           if self.path in chat_routes else payload(body))
            except (ValueError, UnicodeError, RecursionError):
                raise Rejected(400, "invalid_json", "Invalid JSON body") from None
            server = cast("Server", self.server)
            permit = server.chat if self.path in chat_routes else server.embedding
            acquired = permit.acquire(blocking=False)
            if not acquired:
                raise Rejected(429, "busy", "One request of this inference kind may run at a time")
            result = (generate(chat_routes[self.path], request) if self.path in chat_routes
                      else embed(routes[self.path], request))
            self.send_json(200, result)
        except Rejected as error:
            self.send_json(error.status, {"error": {"code": error.code, "message": error.message,
                                                  "type": "invalid_request_error" if error.status < 500 else "server_error"}})
        except (OSError, TimeoutError):
            self.send_json(408, {"error": {"code": "body_timeout", "message": "Request body timed out"}})
        finally:
            if acquired and permit is not None:
                permit.release()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, tokenizer_dir=None):
        self.guard = PromptGuard(tokenizer_dir or Path(__file__).parent / "cpu-tokenizer")
        self.workers = threading.BoundedSemaphore(8)
        # Embeddings and generation run on distinct physical Ollama runtimes.
        # PC and CPU generation still share one admission bound across failover.
        self.embedding = threading.BoundedSemaphore(1)
        self.chat = threading.BoundedSemaphore(1)
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
