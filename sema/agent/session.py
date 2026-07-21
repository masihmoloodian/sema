"""
Chat sessions — on-disk format shared with the VS Code extension.

The layout mirrors ``vscode-extension/src/sessionStore.ts`` exactly so a session
started in the editor resumes in the terminal and vice versa:

    <base>/sessions/<sha1(workspace)[:16]>/<id>.json
    <base>/sessions/<sha1(workspace)[:16]>/attachments/<id>/

Field names are camelCase because the extension owns the schema; the Python
dataclasses translate at the boundary and never leak camelCase inward.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# Where sessions live when the caller does not override it. Matches the path the
# VS Code extension gets from `context.globalStorageUri` on each platform.
_DARWIN_BASE = "Library/Application Support/Code/User/globalStorage/sema.sema"
_LINUX_BASE = ".config/Code/User/globalStorage/sema.sema"


def default_base_dir() -> Path:
    """Best-effort location of the extension's global storage directory.

    Falls back to ``~/.sema/chat`` when VS Code is not installed, which keeps the
    terminal app fully functional standalone.
    """
    override = os.environ.get("SEMA_CHAT_HOME")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    for rel in (_DARWIN_BASE, _LINUX_BASE):
        candidate = home / rel
        if candidate.parent.exists():
            return candidate
    return home / ".sema" / "chat"


def _new_id() -> str:
    """Same shape as the extension's ids: base36 timestamp + 6 random chars."""
    stamp = _base36(int(time.time() * 1000))
    tail = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(6))
    return f"{stamp}-{tail}"


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    out = ""
    while value:
        value, rem = divmod(value, 36)
        out = digits[rem] + out
    return out


@dataclass
class Attachment:
    id: str
    name: str
    kind: str  # image | pdf | text
    mime: str
    size: int


@dataclass
class ChatMessage:
    role: str  # user | assistant
    content: str
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class SessionUsage:
    input: int = 0
    output: int = 0
    cached: int = 0
    cost: float = 0.0
    cost_known: bool = False
    turns: int = 0

    def add(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached: int = 0,
        cost: float | None = None,
    ) -> None:
        self.input += input_tokens
        self.output += output_tokens
        self.cached += cached
        self.turns += 1
        if cost is not None:
            self.cost += cost
            self.cost_known = True


@dataclass
class Session:
    """A full transcript plus everything needed to resume it."""

    id: str
    title: str = "New chat"
    created_at: int = 0
    updated_at: int = 0
    provider: str = "anthropic"
    model: str = ""
    mode: str = "agent"
    effort: str = "default"
    cli_session_id: str | None = None
    cli_session_provider: str | None = None
    cli_session_model: str | None = None
    cli_session_mode: str | None = None
    cli_session_permission: str | None = None
    plan_path: str | None = None
    usage: SessionUsage = field(default_factory=SessionUsage)
    messages: list[ChatMessage] = field(default_factory=list)

    @classmethod
    def create(cls, provider: str, model: str, mode: str = "agent") -> "Session":
        now = int(time.time() * 1000)
        return cls(
            id=_new_id(),
            created_at=now,
            updated_at=now,
            provider=provider,
            model=model,
            mode=mode,
        )

    # ---- serialization (camelCase, extension-compatible) -----------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "provider": self.provider,
            "model": self.model,
            "usage": {
                "input": self.usage.input,
                "output": self.usage.output,
                "cached": self.usage.cached,
                "cost": self.usage.cost,
                "costKnown": self.usage.cost_known,
                "turns": self.usage.turns,
            },
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    **(
                        {"attachments": [asdict(a) for a in m.attachments]}
                        if m.attachments
                        else {}
                    ),
                }
                for m in self.messages
            ],
        }
        # Optional fields are omitted when unset so files written here look
        # byte-identical to the extension's for the same state.
        for key, value in (
            ("cliSessionId", self.cli_session_id),
            ("cliSessionProvider", self.cli_session_provider),
            ("cliSessionModel", self.cli_session_model),
            ("cliSessionMode", self.cli_session_mode),
            ("cliSessionPermission", self.cli_session_permission),
            ("planPath", self.plan_path),
        ):
            if value:
                data[key] = value
        # Terminal-only extras. The extension ignores unknown keys, so round
        # tripping a session through VS Code preserves them.
        data["semaMode"] = self.mode
        data["semaEffort"] = self.effort
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        raw_usage = data.get("usage") or {}
        usage = SessionUsage(
            input=int(raw_usage.get("input", 0)),
            output=int(raw_usage.get("output", 0)),
            cached=int(raw_usage.get("cached", 0)),
            cost=float(raw_usage.get("cost", 0.0)),
            cost_known=bool(raw_usage.get("costKnown", False)),
            turns=int(raw_usage.get("turns", 0)),
        )
        messages = []
        for raw in data.get("messages") or []:
            attachments = [
                Attachment(
                    id=a.get("id", ""),
                    name=a.get("name", ""),
                    kind=a.get("kind", "text"),
                    mime=a.get("mime", "text/plain"),
                    size=int(a.get("size", 0)),
                )
                for a in (raw.get("attachments") or [])
            ]
            messages.append(
                ChatMessage(
                    role=raw.get("role", "user"),
                    content=raw.get("content", ""),
                    attachments=attachments,
                )
            )
        return cls(
            id=data["id"],
            title=data.get("title", "New chat"),
            created_at=int(data.get("createdAt", 0)),
            updated_at=int(data.get("updatedAt", 0)),
            provider=data.get("provider", "anthropic"),
            model=data.get("model", ""),
            mode=data.get("semaMode", "agent"),
            effort=data.get("semaEffort", "default"),
            cli_session_id=data.get("cliSessionId"),
            cli_session_provider=data.get("cliSessionProvider"),
            cli_session_model=data.get("cliSessionModel"),
            cli_session_mode=data.get("cliSessionMode"),
            cli_session_permission=data.get("cliSessionPermission"),
            plan_path=data.get("planPath"),
            usage=usage,
            messages=messages,
        )

    def title_from_messages(self) -> str:
        """First non-empty user line, trimmed — matches the extension's rule."""
        for message in self.messages:
            if message.role != "user":
                continue
            text = message.content.strip()
            if text:
                first_line = text.splitlines()[0].strip()
                return first_line[:60] if len(first_line) <= 60 else first_line[:57] + "..."
            if message.attachments:
                return message.attachments[0].name[:60]
        return "New chat"


@dataclass
class SessionMeta:
    """Row for the session browser — everything but the transcript."""

    id: str
    title: str
    created_at: int
    updated_at: int
    provider: str
    model: str
    message_count: int


class SessionStore:
    """Per-workspace, on-disk session storage."""

    def __init__(self, base_dir: Path | str | None, workspace_key: str) -> None:
        base = Path(base_dir) if base_dir else default_base_dir()
        digest = hashlib.sha1((workspace_key or "_noworkspace").encode()).hexdigest()[:16]
        self.dir = base / "sessions" / digest

    def _file_for(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def attachments_dir(self, session_id: str) -> Path:
        return self.dir / "attachments" / session_id

    def save(self, session: Session) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        session.updated_at = int(time.time() * 1000)
        if session.title in ("", "New chat"):
            session.title = session.title_from_messages()
        path = self._file_for(session.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def load(self, session_id: str) -> Session | None:
        try:
            raw = self._file_for(session_id).read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return Session.from_dict(json.loads(raw))
        except (ValueError, KeyError):
            return None

    def list(self) -> list[SessionMeta]:
        """All sessions, newest activity first."""
        try:
            names = [p for p in self.dir.iterdir() if p.suffix == ".json"]
        except OSError:
            return []
        rows: list[SessionMeta] = []
        for path in names:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or "id" not in data:
                continue
            rows.append(
                SessionMeta(
                    id=data["id"],
                    title=data.get("title", "New chat"),
                    created_at=int(data.get("createdAt", 0)),
                    updated_at=int(data.get("updatedAt", 0)),
                    provider=data.get("provider", ""),
                    model=data.get("model", ""),
                    message_count=len(data.get("messages") or []),
                )
            )
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return rows

    def delete(self, session_id: str) -> None:
        try:
            self._file_for(session_id).unlink()
        except OSError:
            pass
        shutil.rmtree(self.attachments_dir(session_id), ignore_errors=True)

    def prune_attachments(self) -> None:
        """Drop attachment directories whose session file is gone."""
        root = self.dir / "attachments"
        if not root.is_dir():
            return
        live = {p.stem for p in self.dir.glob("*.json")}
        for child in root.iterdir():
            if child.is_dir() and child.name not in live:
                shutil.rmtree(child, ignore_errors=True)
