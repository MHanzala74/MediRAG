"""Custom exceptions for the Medical RAG system."""
import sys, logging
log = logging.getLogger("app")

def _detail(e: Exception) -> str:
    _, _, tb = sys.exc_info()
    if tb is None:
        return str(e)
    return f"[{tb.tb_frame.f_code.co_filename}:{tb.tb_lineno}] {e}"

class RAGException(Exception):
    def __init__(self, e: Exception):
        self.detail = _detail(e)
        log.error("RAGException: %s", self.detail)
        super().__init__(self.detail)

class GuardrailException(Exception):
    """Raised when a guardrail blocks a query."""
    def __init__(self, reason: str):
        self.detail = reason
        super().__init__(reason)
