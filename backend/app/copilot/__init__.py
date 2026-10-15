"""Retrieval-first, evidence-cited copilot (Phase 8, brief P7).

    question -> retrieve evidence items (each with a stable id) -> deterministic,
    cited draft -> optional LLM rephrasing (validated: every citation must exist)

The LLM only writes words. Facts, numbers and actions come from evidence items.
"""
