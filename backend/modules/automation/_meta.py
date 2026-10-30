"""Automation module metadata."""

MODULE_INFO = {
    "name": "automation",
    "purpose": "OCR, receipt/recipe parsing, expiration estimation; future agentic layer.",
    "owns": [],
    "consumes_from": {},
    "invariants": ["Parsers are deterministic-first; LLM/agent writes go through approval (future)."],
}
