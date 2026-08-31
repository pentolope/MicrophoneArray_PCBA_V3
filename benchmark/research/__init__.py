"""Machinery the generic PCBA toolkit does not need, kept beside its callers.

These were extracted from `pcbqa`, which has no concept of Board A, Board B or
an A/B experiment, and no cross-run artifact cache to keep fresh:

    benchmark.py    a typed metric contract for an A/B comparison
    compute.py      a compute-spend ledger for a placement search
    progression.py  a candidate correctness-class ordering, for ranking
    freshness.py    producer closures, so a persisted pool, decision or
                    comparison report knows when it has gone stale

Nothing here validates a board. Validation is the toolkit's, and a candidate's
correctness is whatever its gates say.
"""
