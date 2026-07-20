"""Redaction for Kubernetes Secret values — closes a real gap plain regex misses.

`kubectl get secret ... -o yaml/json` returns Secret data **base64-encoded**,
not plaintext. None of secrets.py's patterns match a base64 blob (that's the
whole point of base64: it doesn't look like the thing it encodes), so a raw
AWS key or password sails straight through the regex/NER redaction layers
unredacted, even though `base64 -d` recovers it trivially. `kubectl describe
secret` already masks values to byte counts by kubectl's own default and
needs no help; `get` does not.

This module is deliberately scoped to commands that read a Secret resource —
outside that context, base64-looking substrings are common and mostly
harmless (git hashes, image digests, ...), so blanket base64 redaction
elsewhere would be noisy and would erode trust in what redaction actually
means. Inside that context, any value is presumptively sensitive by
definition, so redaction is aggressive on purpose.
"""

from __future__ import annotations

import base64
import json
import re

_DATA_HEADER_RE = re.compile(r"^(\s*)(data|stringData):\s*$")
_KV_LINE_RE = re.compile(r"^(\s+)([A-Za-z0-9_.\-]+):\s*(.*)$")
_B64_BLOB_RE = re.compile(r"\b[A-Za-z0-9+/]{6,}={0,2}\b")
_REDACTED = "[K8S_SECRET_VALUE]"


def touches_secret_resource(argv: list[str]) -> bool:
    """True if a kubectl argv (without the 'kubectl' binary itself) reads a Secret.

    Covers `get secret ...`, `get secret/name`, and `get secrets ...` — the
    verbs that can actually return Secret data (as opposed to `describe`,
    which kubectl already masks by default).
    """
    if not argv or argv[0] != "get":
        return False
    for tok in argv[1:]:
        if tok.startswith("-"):
            continue
        head = tok.split("/", 1)[0].lower()
        return head in {"secret", "secrets"}
    return False


def _redact_yaml(text: str) -> str:
    lines = text.splitlines()
    out = []
    in_block = False
    block_indent = 0
    for line in lines:
        header = _DATA_HEADER_RE.match(line)
        if header:
            in_block = True
            block_indent = len(header.group(1))
            out.append(line)
            continue
        if in_block:
            indent = len(line) - len(line.lstrip(" "))
            kv = _KV_LINE_RE.match(line)
            if kv and indent > block_indent:
                out.append(f"{kv.group(1)}{kv.group(2)}: {_REDACTED}")
                continue
            if line.strip() == "" :
                out.append(line)
                continue
            in_block = False  # dedented past the data/stringData block
        out.append(line)
    return "\n".join(out)


def _redact_json_obj(obj):
    if isinstance(obj, dict):
        if obj.get("kind") == "Secret":
            for key in ("data", "stringData"):
                if isinstance(obj.get(key), dict):
                    obj[key] = {k: _REDACTED for k in obj[key]}
        if isinstance(obj.get("items"), list):
            obj["items"] = [_redact_json_obj(item) for item in obj["items"]]
        return obj
    return obj


_BARE_EXTRACTION_FORMATS = ("jsonpath", "jsonpath-as-json", "go-template", "go-template-file", "custom-columns", "custom-columns-file")


def _uses_bare_extraction_format(argv: list[str]) -> bool:
    """True if -o/--output requests a format that prints a bare value with no
    surrounding structure (jsonpath, go-template, custom-columns, ...).

    This is the ONLY case where blanket base64-blob redaction is safe to
    apply: a `kubectl get secret` in this format is, by the Kubernetes API's
    own contract, extracting a `.data.*`/`.stringData.*` field — which is
    always base64 — so a successful decode here really is that Secret's
    data, not a coincidence. Anything else that reaches the blob fallback
    (a connection error, `describe`-style text, ...) is NOT known to be
    Secret data, and English words happen to be valid base64 alphabet too
    (any word of 6+ letters "decodes" without error) — so applying the blob
    pass there over-redacts plain text instead of protecting a real value.
    """
    value = _flag_value(argv, {"-o", "--output"})
    if value is None:
        return False
    fmt = value.split("=", 1)[0]
    return fmt in _BARE_EXTRACTION_FORMATS


def _flag_value(argv: list[str], names: set[str]) -> str | None:
    for i, tok in enumerate(argv):
        for name in names:
            if tok == name and i + 1 < len(argv):
                return argv[i + 1]
            if tok.startswith(name + "="):
                return tok.split("=", 1)[1]
    return None


def _redact_base64_blobs(text: str) -> str:
    def _sub(m: re.Match) -> str:
        token = m.group(0)
        padded = token + "=" * (-len(token) % 4)
        try:
            base64.b64decode(padded, validate=True)
        except Exception:
            return token
        return _REDACTED
    return _B64_BLOB_RE.sub(_sub, text)


def redact_secret_output(text: str, argv: list[str] | None = None) -> str:
    """Redact Secret data from `kubectl get secret` output, any -o format.

    Tries structured redaction first (JSON, then YAML's `data:`/`stringData:`
    blocks) so surrounding output stays readable. Falls back to blanket
    base64-blob redaction ONLY when `argv` shows the command used a bare-value
    extraction format (jsonpath/go-template/custom-columns) — that's the one
    case where "no structure to parse" really means "the whole output is a
    Secret data value", as opposed to e.g. a connection-error message that
    happens to contain no YAML/JSON either. Without that argv context, plain
    English text (any word of 6+ letters is valid base64 alphabet) would get
    false-positive redacted — caught in real testing via the VS Code
    extension, see docs/devops-guard-plan.md.
    """
    if not text.strip():
        return text

    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(text)
            return json.dumps(_redact_json_obj(obj), indent=2)
        except (json.JSONDecodeError, TypeError):
            pass

    if "data:" in text or "stringData:" in text:
        # Structural redaction handled it — do NOT also run the blanket
        # base64-blob pass below, or ordinary keys (apiVersion, kind, uid,
        # ...) get caught too since they're valid base64 alphabet themselves.
        return _redact_yaml(text)

    if argv is not None and _uses_bare_extraction_format(argv):
        return _redact_base64_blobs(text)

    # No recognizable Secret structure AND not a bare-extraction command —
    # e.g. a connection error, or `describe`-style text. Nothing here is
    # known to be Secret data, so leave it to the normal regex/NER layers
    # rather than risk redacting ordinary words.
    return text
