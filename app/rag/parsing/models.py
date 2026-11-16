"""Parser-neutral document model: what every parser returns and the chunker consumes."""
from dataclasses import dataclass, field


class ParseError(RuntimeError):
    """The file could not be parsed (corrupt, encrypted, empty …). Message is safe to show to an admin."""


class ParserUnavailable(ParseError):
    """The requested parser (Docling) or one of its optional pieces is not installed."""


@dataclass
class Element:
    kind: str              # "text" | "table" | "figure"
    text: str
    page: int | None       # 1-based; None for formats without pages (DOCX, HTML, Markdown)
    section: str = ""      # heading path, e.g. "Diabetes Guide > 2. Symptoms"


@dataclass
class ParsedDocument:
    elements: list[Element]
    parser: str                          # "pypdf" | "docling"
    n_pages: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def n_chars(self) -> int:
        return sum(len(e.text) for e in self.elements)
