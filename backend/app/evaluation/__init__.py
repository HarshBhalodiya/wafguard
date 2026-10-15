"""Evaluation-side helpers (Phase 3).

Modules here are allowed to read the generator's hidden labels
(`ground_truth.parquet`) because they are *evaluation* code: they build
measured artifacts (e.g. the pattern→cause association table) and score
candidate rankings against ground truth. Nothing under `app/ml`,
`app/features`, or `app/services` imports this package at training or
inference time — services only read the versioned JSON artifacts that
these builders write (enforced by tests/test_ground_truth_leakage.py).
"""
