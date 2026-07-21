"""
Attachment staging — mirrors ``vscode-extension/src/attachments.ts``.

Bytes live on disk under the session's attachment directory, named by id; the
transcript holds metadata only. Limits and sniffing rules match the extension so
a session opened in either surface sees the same accepted set.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from .session import Attachment, ChatMessage

LIMITS = {
    "image": 5 * 1024 * 1024,
    "pdf": 20 * 1024 * 1024,
    "text": 256 * 1024,
    "total": 20 * 1024 * 1024,
}

IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".rs", ".rb", ".php",
    ".java", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".scala", ".sh",
    ".bash", ".zsh", ".fish", ".sql", ".html", ".css", ".scss", ".less", ".xml", ".svg",
    ".diff", ".patch", ".gradle", ".tf", ".dockerfile", ".makefile", ".lua", ".pl", ".r",
}

# Magic-number prefixes, checked before the extension so a mislabeled file is
# classified by what it actually is.
_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image", "image/png"),
    (b"\xff\xd8\xff", "image", "image/jpeg"),
    (b"GIF87a", "image", "image/gif"),
    (b"GIF89a", "image", "image/gif"),
    (b"%PDF-", "pdf", "application/pdf"),
]


def sniff(name: str, data: bytes) -> tuple[str, str] | None:
    """Return ``(kind, mime)`` for a file, or None when unsupported."""
    for sig, kind, mime in _SIGNATURES:
        if data.startswith(sig):
            return kind, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "image/webp"
    suffix = Path(name).suffix.lower()
    if suffix in IMAGE_MIME:
        return "image", IMAGE_MIME[suffix]
    if suffix == ".pdf":
        return "pdf", "application/pdf"
    if suffix in TEXT_EXT or Path(name).name.lower() in {"dockerfile", "makefile"}:
        return "text", mimetypes.guess_type(name)[0] or "text/plain"
    # An extensionless file that decodes as UTF-8 is treated as text.
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "text", "text/plain"


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def check_limit(kind: str, size: int) -> str | None:
    """Return an error message when the file is too large, else None."""
    cap = LIMITS.get(kind)
    if cap is not None and size > cap:
        return f"{kind} attachments are capped at {format_size(cap)} (this one is {format_size(size)})"
    return None


def total_bytes(messages: list[ChatMessage]) -> int:
    return sum(a.size for m in messages for a in m.attachments)


def stage(directory: Path, source: Path) -> Attachment:
    """Copy a file into the session's attachment directory.

    Raises ``ValueError`` with a user-facing message when the file is
    unsupported or over the per-kind cap.
    """
    source = source.expanduser()
    if not source.is_file():
        raise ValueError(f"Not a file: {source}")
    data = source.read_bytes()
    sniffed = sniff(source.name, data)
    if sniffed is None:
        raise ValueError(f"Unsupported attachment type: {source.name}")
    kind, mime = sniffed
    error = check_limit(kind, len(data))
    if error:
        raise ValueError(error)
    directory.mkdir(parents=True, exist_ok=True)
    attachment_id = f"{abs(hash((source.name, len(data), source.stat().st_mtime_ns))):x}"
    (directory / attachment_id).write_bytes(data)
    return Attachment(
        id=attachment_id, name=source.name, kind=kind, mime=mime, size=len(data)
    )


def unstage(directory: Path, attachment_id: str) -> None:
    try:
        (directory / attachment_id).unlink()
    except OSError:
        pass


def read_bytes(directory: Path, attachment: Attachment) -> bytes:
    return (directory / attachment.id).read_bytes()


def read_base64(directory: Path, attachment: Attachment) -> str:
    return base64.b64encode(read_bytes(directory, attachment)).decode("ascii")


def read_text(directory: Path, attachment: Attachment) -> str:
    return read_bytes(directory, attachment).decode("utf-8", errors="replace")


def text_block(name: str, body: str) -> str:
    """Wrap an inlined text attachment the same way the extension does."""
    return f"\n\n--- attached file: {name} ---\n{body}\n--- end of {name} ---\n"


def materialize(
    directory: Path, messages: list[ChatMessage]
) -> tuple[list[ChatMessage], list[Attachment]]:
    """Inline text attachments into content; return the binary ones separately.

    Only image/pdf attachments reach a provider as structured blocks — text is
    folded into the message body so every provider, including the text-only
    ones, sees it.
    """
    out: list[ChatMessage] = []
    binaries: list[Attachment] = []
    for message in messages:
        content = message.content
        keep: list[Attachment] = []
        for attachment in message.attachments:
            if attachment.kind == "text":
                try:
                    content += text_block(attachment.name, read_text(directory, attachment))
                except OSError:
                    content += text_block(attachment.name, "(file missing)")
            else:
                keep.append(attachment)
                binaries.append(attachment)
        out.append(ChatMessage(role=message.role, content=content, attachments=keep))
    return out, binaries
