"""
Anthropic Messages API → OpenAI Responses API converter.
Run: uv run python anthropic_proxy.py
Then set: ANTHROPIC_BASE_URL=http://localhost:8765 in settings.json
"""
import http.server
import json
import urllib.request
import urllib.error
import uuid
import time
import sys
import os

TARGET = "https://api.clawplan.ai/v1/responses"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8765

def ts():
    return time.strftime("%H:%M:%S")


# -------------------------- conversion --------------------------

def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            typ = block.get("type")
            if typ == "text":
                parts.append(str(block.get("text", "")))
            elif typ == "tool_result":
                tool_content = block.get("content", "")
                if isinstance(tool_content, list):
                    parts.append(_content_to_text(tool_content))
                else:
                    parts.append(str(tool_content))
        return "\n".join(p for p in parts if p)
    return str(content)


def _anthropic_content_to_responses_input(role: str, content):
    """Convert Anthropic message content blocks to Responses input items.

    Claude Code relies on assistant tool_use blocks followed by user
    tool_result blocks. Dropping those blocks breaks the tool loop.
    """
    if not isinstance(content, list):
        return [{"role": role, "content": str(content)}]

    items = []
    text_parts = []
    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue

        typ = block.get("type")
        if typ == "text":
            text_parts.append(str(block.get("text", "")))
        elif typ == "tool_use":
            if text_parts:
                items.append({"role": role, "content": "\n".join(p for p in text_parts if p)})
                text_parts = []
            items.append({
                "type": "function_call",
                "call_id": block.get("id", ""),
                "name": block.get("name", ""),
                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
            })
        elif typ == "tool_result":
            if text_parts:
                items.append({"role": role, "content": "\n".join(p for p in text_parts if p)})
                text_parts = []
            items.append({
                "type": "function_call_output",
                "call_id": block.get("tool_use_id", ""),
                "output": _content_to_text(block.get("content", "")),
            })

    if text_parts:
        items.append({"role": role, "content": "\n".join(p for p in text_parts if p)})
    return [item for item in items if item.get("content") or item.get("type")]


def anthropic_to_responses(an_req: dict) -> dict:
    resp = {}
    resp["model"] = an_req.get("model", "gpt-5.5")
    # Upstream API requires stream=True, so we always set it to True
    resp["stream"] = True

    if "max_tokens" in an_req:
        resp["max_output_tokens"] = an_req["max_tokens"]
    if "temperature" in an_req:
        resp["temperature"] = an_req["temperature"]
    if "top_p" in an_req:
        resp["top_p"] = an_req["top_p"]

    # system prompt
    instructions = None
    if isinstance(an_req.get("system"), str):
        instructions = an_req["system"]
    elif isinstance(an_req.get("system"), list):
        instructions = "\n".join(
            p["text"] for p in an_req["system"]
            if isinstance(p, dict) and p.get("type") == "text"
        )
    if instructions:
        resp["instructions"] = instructions

    # convert messages → input
    input_items = []
    for msg in an_req.get("messages", []):
        role = msg.get("role", "user")
        if role == "system":
            if not instructions:
                content = msg.get("content", "")
                if isinstance(content, list):
                    instructions = "\n".join(
                        c["text"] for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                else:
                    instructions = str(content)
                resp["instructions"] = instructions
            continue

        input_items.extend(_anthropic_content_to_responses_input(role, msg.get("content", "")))

    resp["input"] = input_items

    # tools
    tools = an_req.get("tools") or []
    if tools:
        resp_tools = []
        for t in tools:
            resp_tools.append({
                "type": "function",
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            })
        resp["tools"] = resp_tools

    return resp


def _anthropic_sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


class ResponsesToAnthropicSSE:
    """Stateful Responses SSE -> Anthropic Messages SSE converter."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.message_started = False
        self.open_block_index = None
        self.open_block_type = None
        self.next_index = 0
        self.stop_reason = "end_turn"
        self.current_tool_index = None
        self.tool_arg_streamed = False

    def _message_start(self):
        if self.message_started:
            return []
        self.message_started = True
        return [_anthropic_sse("message_start", {
            "type": "message_start",
            "message": {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": self.model_id,
                "stop_reason": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })]

    def _close_block(self):
        if self.open_block_index is None:
            return []
        index = self.open_block_index
        self.open_block_index = None
        self.open_block_type = None
        self.current_tool_index = None
        return [_anthropic_sse("content_block_stop", {
            "type": "content_block_stop",
            "index": index,
        })]

    def _start_text_block(self):
        out = self._message_start()
        if self.open_block_type == "text":
            return out
        out.extend(self._close_block())
        index = self.next_index
        self.next_index += 1
        self.open_block_index = index
        self.open_block_type = "text"
        out.append(_anthropic_sse("content_block_start", {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        }))
        return out

    def _start_tool_block(self, item: dict):
        out = self._message_start()
        out.extend(self._close_block())
        index = self.next_index
        self.next_index += 1
        call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:16]}"
        self.open_block_index = index
        self.open_block_type = "tool_use"
        self.current_tool_index = index
        self.tool_arg_streamed = False
        self.stop_reason = "tool_use"
        out.append(_anthropic_sse("content_block_start", {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "tool_use",
                "id": call_id,
                "name": item.get("name", ""),
                "input": {},
            },
        }))
        arguments = item.get("arguments")
        if arguments:
            out.append(self._tool_delta(index, arguments))
        return out

    def _text_delta(self, text: str):
        if not text:
            return []
        out = self._start_text_block()
        out.append(_anthropic_sse("content_block_delta", {
            "type": "content_block_delta",
            "index": self.open_block_index,
            "delta": {"type": "text_delta", "text": text},
        }))
        return out

    def _tool_delta(self, index: int, partial_json: str):
        return _anthropic_sse("content_block_delta", {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial_json},
        })

    def _complete(self, response: dict):
        out = self._message_start()
        out.extend(self._close_block())
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        out_tok = usage.get("output_tokens", 0)
        stop = "max_tokens" if response.get("incomplete_details") else self.stop_reason
        out.append(_anthropic_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": out_tok},
        }))
        out.append(_anthropic_sse("message_stop", {"type": "message_stop"}))
        return out

    def convert(self, data: dict):
        typ = data.get("type", "")

        if typ in ("response.created", "response.in_progress"):
            return self._message_start()

        if typ == "response.output_item.added":
            item = data.get("item", {})
            item_type = item.get("type", "")
            if item_type == "message" and item.get("role") == "assistant":
                return self._message_start()
            if item_type in ("function_call", "tool_call"):
                return self._start_tool_block(item)
            if item_type in ("text", "output_text"):
                return self._text_delta(item.get("text", ""))
            return []

        if typ in ("response.output_text.delta", "response.refusal.delta"):
            return self._text_delta(data.get("delta", ""))

        if typ in ("response.output_text.done", "response.refusal.done"):
            return self._close_block()

        if typ in ("response.content_part.done", "response.content_part.added"):
            part = data.get("part", {})
            if part.get("type") in ("text", "output_text"):
                return self._text_delta(part.get("text", ""))
            return []

        if typ in ("response.function_call_added", "response.tool_call.added"):
            return self._start_tool_block(data.get("item", data))

        if typ in ("response.function_call_arguments.delta", "response.tool_call.arguments.delta"):
            if self.open_block_type != "tool_use":
                return []
            delta = data.get("delta", "")
            if delta:
                self.tool_arg_streamed = True
            return [self._tool_delta(self.open_block_index, delta)] if delta else []

        if typ in ("response.function_call_arguments.done", "response.tool_call.arguments.done"):
            out = []
            if self.open_block_type == "tool_use":
                arguments = data.get("arguments", "")
                if arguments and not self.tool_arg_streamed:
                    out.append(self._tool_delta(self.open_block_index, arguments))
                out.extend(self._close_block())
            return out

        if typ == "response.output_item.done":
            item = data.get("item", {})
            if item.get("type") in ("function_call", "tool_call"):
                out = []
                if self.open_block_type != "tool_use":
                    out.extend(self._start_tool_block(item))
                arguments = item.get("arguments", "")
                if arguments and self.open_block_type == "tool_use" and not self.tool_arg_streamed:
                    out.append(self._tool_delta(self.open_block_index, arguments))
                out.extend(self._close_block())
                return out
            return []

        if typ in ("response.completed", "response.incomplete"):
            return self._complete(data.get("response", {}))

        if typ == "response.failed":
            return self._complete(data.get("response", {}))

        print(f"[{time.strftime('%H:%M:%S')}]   unknown upstream event type: {typ}")
        return []


# ------------------------------- server -------------------------------

class Handler(http.server.BaseHTTPRequestHandler):

    def _respond(self, code, body, ct="application/json"):
        body = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_auth(self):
        # Claude Code sends x-api-key; clawplan expects Authorization: Bearer
        for h in ("x-api-key", "Authorization"):
            v = self.headers.get(h, "")
            if v:
                if h == "x-api-key":
                    # Claude Code format: just the token (no "Bearer" prefix)
                    return f"Bearer {v}"
                return v  # Already has Bearer prefix
        return ""

    # ---- routes ----

    def do_GET(self):
        try:
            if self.path in ("/v1/models", "/v1/models/"):
                self._models()
            elif self.path in ("/health", "/"):
                self._respond(200, {"ok": True})
            else:
                self._respond(404, {"error": "not found"})
        except Exception as e:
            print(f"[{ts()}] GET {self.path} ERROR: {e}", file=sys.stderr)

    def do_POST(self):
        try:
            if self.path == "/v1/messages" or self.path.startswith("/v1/messages?"):
                self._messages()
            else:
                self._respond(404, {"error": "not found"})
        except Exception as e:
            print(f"[{ts()}] POST {self.path} ERROR: {e}", file=sys.stderr)
            try:
                self._respond(500, {"error": str(e)})
            except Exception:
                pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    # ---- handlers ----

    def _models(self):
        now = int(time.time())
        self._respond(200, {
            "data": [
                {"id": "gpt-5.5", "object": "model", "created": now, "display_name": "GPT-5.5"},
                {"id": "gpt-5.4", "object": "model", "created": now, "display_name": "GPT-5.4"},
                {"id": "gpt-5.4-mini", "object": "model", "created": now, "display_name": "GPT-5.4 Mini"},
                {"id": "gpt-5.3-codex", "object": "model", "created": now, "display_name": "GPT-5.3 Codex"},
            ],
            "has_more": False,
        })

    def _messages(self):
        # Read request
        cl = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(cl) if cl else b"{}"
        req = json.loads(body)

        model = req.get("model", "gpt-5.5")
        stream = req.get("stream", False)
        print(f"[{ts()}] POST /v1/messages  model={model}  stream={stream}")

        # Convert
        upstream_req = anthropic_to_responses(req)
        upstream_body = json.dumps(upstream_req, ensure_ascii=False).encode()

        # Forward
        auth = self._get_auth()
        if not auth:
            # Fallback: use environment variable ANTHROPIC_AUTH_TOKEN
            env_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
            if env_token:
                auth = f"Bearer {env_token}"
            else:
                # Last resort: hardcoded token (may be outdated)
                auth = "Bearer sk-RJteQXmihqWOrHFnhb9MOuUOPGEgomO5MrCzwQjBzTWmGSFv"

        http_req = urllib.request.Request(TARGET, data=upstream_body, method="POST")
        http_req.add_header("Content-Type", "application/json")
        http_req.add_header("Authorization", auth)
        http_req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        http_req.add_header("Accept", "text/event-stream, application/json")

        try:
            resp = urllib.request.urlopen(http_req, timeout=300)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            print(f"[{ts()}]   upstream error {e.code}: {err[:200]}")
            self._respond(e.code, {"error": err})
            return
        except Exception as e:
            print(f"[{ts()}]   upstream connect error: {e}")
            self._respond(502, {"error": f"Upstream error: {e}"})
            return

        if not stream:
            # Collect streamed events and build a non‑streaming Anthropic response
            buf = b""
            collected_text = []
            tool_calls = []
            final_response = None
            tool_arg_buffers = {}
            current_tool_call_id = None

            try:
                while True:
                    chunk = resp.fp.read(4096)
                    if not chunk:
                        break
                    buf += chunk

                    # Parse SSE frames
                    while b"\n\n" in buf:
                        frame, buf = buf.split(b"\n\n", 1)
                        frame = frame.decode("utf-8", errors="replace")

                        # Extract data field
                        data_str = ""
                        for line in frame.split("\n"):
                            if line.startswith("data: "):
                                data_str = line[6:]
                            elif line.startswith("data:"):
                                data_str = line[5:]

                        if not data_str or data_str.strip() == "[DONE]":
                            continue

                        try:
                            event_data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        evt_type = event_data.get("type", "unknown")
                        print(f"[{ts()}]   upstream event (non‑stream): {evt_type}")

                        # Collect text from various event types
                        if evt_type == "response.output_text.delta":
                            delta = event_data.get("delta", "")
                            collected_text.append(delta)
                        elif evt_type == "response.output_item.added":
                            item = event_data.get("item", {})
                            if item.get("type") == "text":
                                text = item.get("text", "")
                                if text:
                                    collected_text.append(text)
                            elif item.get("type") in ("function_call", "tool_call"):
                                call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:16]}"
                                current_tool_call_id = call_id
                                tool_arg_buffers.setdefault(call_id, item.get("arguments", ""))
                                tool_calls.append({
                                    "type": "tool_use",
                                    "id": call_id,
                                    "name": item.get("name", ""),
                                    "input": {},
                                })
                        elif evt_type == "response.content_part.done":
                            # This event may contain text content
                            part = event_data.get("part", {})
                            if part.get("type") == "text":
                                text = part.get("text", "")
                                if text:
                                    collected_text.append(text)
                        elif evt_type in ("response.function_call_added", "response.tool_call.added"):
                            item = event_data.get("item", event_data)
                            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:16]}"
                            current_tool_call_id = call_id
                            tool_arg_buffers.setdefault(call_id, item.get("arguments", ""))
                            tool_calls.append({
                                "type": "tool_use",
                                "id": call_id,
                                "name": item.get("name", ""),
                                "input": {},
                            })
                        elif evt_type in ("response.function_call_arguments.delta", "response.tool_call.arguments.delta"):
                            if current_tool_call_id:
                                tool_arg_buffers[current_tool_call_id] = (
                                    tool_arg_buffers.get(current_tool_call_id, "") + event_data.get("delta", "")
                                )
                        elif evt_type in ("response.function_call_arguments.done", "response.tool_call.arguments.done"):
                            if current_tool_call_id and event_data.get("arguments") and not tool_arg_buffers.get(current_tool_call_id):
                                tool_arg_buffers[current_tool_call_id] = event_data.get("arguments", "")
                        elif evt_type == "response.output_item.done":
                            item = event_data.get("item", {})
                            if item.get("type") in ("function_call", "tool_call"):
                                call_id = item.get("call_id") or item.get("id") or current_tool_call_id or f"call_{uuid.uuid4().hex[:16]}"
                                current_tool_call_id = call_id
                                if not any(t["id"] == call_id for t in tool_calls):
                                    tool_calls.append({
                                        "type": "tool_use",
                                        "id": call_id,
                                        "name": item.get("name", ""),
                                        "input": {},
                                    })
                                if item.get("arguments") and not tool_arg_buffers.get(call_id):
                                    tool_arg_buffers[call_id] = item.get("arguments", "")
                        # Store the final response for usage and stop reason
                        elif evt_type == "response.completed":
                            final_response = event_data.get("response", {})
            except Exception as e:
                print(f"[{ts()}]   error reading stream (non‑stream): {e}")
                self._respond(500, {"error": f"Failed to collect stream: {e}"})
                return

            # Build Anthropic‑style response
            # Basic structure matching Anthropic Messages API non‑streaming response
            full_text = "".join(collected_text)
            for tool_call in tool_calls:
                args = tool_arg_buffers.get(tool_call["id"], "")
                if args:
                    try:
                        tool_call["input"] = json.loads(args)
                    except json.JSONDecodeError:
                        tool_call["input"] = {"_raw": args}
            content_blocks = []
            if full_text:
                content_blocks.append({"type": "text", "text": full_text})
            content_blocks.extend(tool_calls)
            anthropic_resp = {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "content": content_blocks,
                "model": model,
                "stop_reason": "tool_use" if tool_calls else "end_turn",
                "usage": final_response.get("usage", {"input_tokens": 0, "output_tokens": len(full_text)}) if final_response else {"input_tokens": 0, "output_tokens": len(full_text)},
            }
            # Override stop reason if we have incomplete details
            if final_response and final_response.get("incomplete_details"):
                anthropic_resp["stop_reason"] = "max_tokens"

            print(f"[{ts()}]   built non‑stream response: {len(full_text)} chars, stop_reason={anthropic_resp['stop_reason']}")
            self._respond(200, anthropic_resp)
            return

        # === streaming ===
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        buf = b""
        converter = ResponsesToAnthropicSSE(model)
        try:
            while True:
                chunk = resp.fp.read(4096)
                if not chunk:
                    break
                buf += chunk

                # Parse SSE frames
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    frame = frame.decode("utf-8", errors="replace")

                    # Extract data field
                    data_str = ""
                    for line in frame.split("\n"):
                        if line.startswith("data: "):
                            data_str = line[6:]
                        elif line.startswith("data:"):
                            data_str = line[5:]

                    if not data_str or data_str.strip() == "[DONE]":
                        continue

                    try:
                        event_data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Log event type for debugging
                    evt_type = event_data.get("type", "unknown")
                    print(f"[{ts()}]   upstream event: {evt_type}")

                    try:
                        sse_lines = converter.convert(event_data)
                        if sse_lines:
                            print(f"[{ts()}]   generated {len(sse_lines)} SSE lines for {evt_type}")
                            for sse_line in sse_lines:
                                self.wfile.write(sse_line.encode())
                                self.wfile.flush()
                        else:
                            print(f"[{ts()}]   no SSE lines generated for {evt_type}")
                    except Exception as e:
                        print(f"[{ts()}]   error converting event: {e}")
        except Exception as e:
            print(f"[{ts()}]   stream error: {e}")

        print(f"[{ts()}]   done  model={model}")

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print(f"[{ts()}] Anthropic→Responses proxy on http://{LISTEN_HOST}:{LISTEN_PORT}")
    print(f"[{ts()}] Upstream: {TARGET}")
    print(f"[{ts()}] Configure settings.json: ANTHROPIC_BASE_URL=http://localhost:{LISTEN_PORT}")
    httpd = http.server.HTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[{ts()}] Shutdown.")
        httpd.shutdown()
