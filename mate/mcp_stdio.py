"""A small MCP stdio client, so a Python script can use the same servers the
agent uses.

The Hermes runtime speaks MCP for us at conversation time. This exists for the
deterministic path: scripts, the spike, and tests that must not depend on a
model being in the loop.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"

# Flipping this on is a risk in a step outside our control: judges install and
# run this agent. The guard is code, not a promise in a README.
FORBIDDEN_ARGS = {"--ignore-robots-txt"}
FORBIDDEN_KEYS = {"ignorerobotstext"}


class MCPError(RuntimeError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    schema: dict


class MCPClient:
    def __init__(self, command: str, args: list[str], *, env: dict | None = None,
                 timeout: float = 120.0):
        bad = FORBIDDEN_ARGS.intersection(args)
        if bad:
            raise MCPError(f"refusing to launch with robots.txt override: {sorted(bad)}")
        self.command, self.args, self.env, self.timeout = command, args, env, timeout
        self._proc: subprocess.Popen | None = None
        self._out: queue.Queue = queue.Queue()
        self._err: list[str] = []
        self._id = 0

    def __enter__(self) -> "MCPClient":
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=self.env,
        )
        threading.Thread(target=self._pump, args=(self._proc.stdout,), daemon=True).start()
        threading.Thread(target=self._drain, args=(self._proc.stderr,), daemon=True).start()
        self._handshake()
        return self

    def __exit__(self, *exc) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _pump(self, stream) -> None:
        for line in stream:
            line = line.strip()
            if line:
                self._out.put(line)

    def _drain(self, stream) -> None:
        for line in stream:
            self._err.append(line.rstrip())
            del self._err[:-50]

    def _send(self, payload: dict) -> None:
        if not self._proc or self._proc.poll() is not None:
            raise MCPError(f"server exited: {self.stderr_tail()}")
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        while True:
            try:
                raw = self._out.get(timeout=self.timeout)
            except queue.Empty:
                raise MCPError(f"{method} timed out after {self.timeout:g}s: {self.stderr_tail()}")
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue  # servers log to stdout sometimes; skip what is not a message
            if msg.get("id") != self._id:
                continue
            if "error" in msg:
                raise MCPError(f"{method}: {msg['error'].get('message', msg['error'])}")
            return msg.get("result", {})

    def _handshake(self) -> None:
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mate", "version": "0.1.0"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[Tool]:
        result = self._request("tools/list")
        return [
            Tool(t.get("name", ""), t.get("description", ""), t.get("inputSchema", {}))
            for t in result.get("tools", [])
        ]

    def call_tool(self, name: str, arguments: dict) -> dict | list | str:
        sneaky = [k for k in arguments if k.lower() in FORBIDDEN_KEYS]
        if sneaky:
            raise MCPError(f"refusing robots.txt override argument: {sneaky}")
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPError(f"{name}: {_text_of(result)}")
        text = _text_of(result)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def stderr_tail(self) -> str:
        return " | ".join(self._err[-5:]) or "(no stderr)"


def _text_of(result: dict) -> str:
    return "\n".join(
        c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
    )


IMAGE_ENTRY = "/opt/plow/mate/airbnb-mcp/node_modules/@openbnb/mcp-server-airbnb/dist/index.js"


def airbnb_client(timeout: float = 120.0) -> MCPClient:
    """The image installs this server at build time, so in a container we launch
    the copy config.yaml already names. Outside one, npx fetches it."""
    entry = os.environ.get("MATE_AIRBNB_MCP_ENTRY", IMAGE_ENTRY)
    if entry and Path(entry).is_file():
        return MCPClient("node", [entry], timeout=timeout)
    return MCPClient("npx", ["-y", "@openbnb/mcp-server-airbnb"], timeout=timeout)
