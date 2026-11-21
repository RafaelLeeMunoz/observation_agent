"""Deterministic pipeline for the observation agent.

Code, not the model, does everything that must be exact: parsing, deduplication,
dates, counting, arithmetic, citation checks and the server. The rules each module
implements are in PLAN.md section 3.1 (R1-R8).
"""
