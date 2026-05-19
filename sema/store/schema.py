from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ChunkType = Literal["function", "class", "method", "interface", "struct", "module", "section", "config"]


@dataclass
class Chunk:
    # Required identity fields
    id: str                          # globally unique: "path/to/file.ts::FunctionName"
    file: str                        # relative path from project root
    language: str                    # "typescript" | "python" | "go"
    chunk_type: ChunkType
    name: str                        # symbol name

    # Content — stored separately, returned on demand
    signature: str                   # one-line summary, ~15 tokens
    body: str                        # full source, ~200 tokens

    # Navigation
    start_line: int
    end_line: int

    # Optional enrichment
    docstring: str | None = None
    imports: list[str] = field(default_factory=list)
    exports: bool = False
    parent_name: str | None = None   # for methods: the class name

    def embed_text(self) -> str:
        """Text to embed — signature + context, not full body."""
        if self.chunk_type in ("section", "config"):
            return f"{self.chunk_type} {self.name}:\n{self.body[:400]}"

        # Qualified name: "AuthService.forgotPassword" makes class context searchable
        qualified = f"{self.parent_name}.{self.name}" if self.parent_name else self.name
        parts = [f"{self.chunk_type} {qualified}: {self.signature}"]

        # Module hint from file path: "apps/api/src/auth/auth.service.ts" → "auth"
        # Helps queries like "auth service update" find methods whose names don't contain "auth"
        module = Path(self.file).stem.split(".")[0]
        if module and module.lower() not in qualified.lower():
            parts.append(f"in {module}")

        if self.docstring:
            parts.append(self.docstring[:200])

        # First 4 body lines — captures decorators/annotations and early statements
        # that reveal what the function does (works for any framework or language).
        # Skipped for class/struct/interface: their bodies are field/schema declarations
        # which add noise rather than signal.
        if self.body and self.chunk_type in ("function", "method"):
            lines = [ln.strip() for ln in self.body.splitlines() if ln.strip()]
            preview = " ".join(lines[:4])
            if preview:
                parts.append(preview[:250])

        return "\n".join(parts)

    def to_search_result(self) -> dict:
        """Minimal result returned by search_code() — signatures only."""
        return {
            "id": self.id,
            "file": self.file,
            "name": self.name,
            "type": self.chunk_type,
            "signature": self.signature,
            "start_line": self.start_line,
            "language": self.language,
        }
