"""Deterministic tool/skill selection accuracy eval.

Assets: ``queries.yaml`` (versioned query -> expected-target pairs) and
``corpus_snapshot.yaml`` (frozen tool/skill names + descriptions). Runner:
``run_eval`` — lexical scoring only, no LLM, no network, safe for CI.
"""
