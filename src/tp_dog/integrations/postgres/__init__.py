"""Postgres database integration package — re-exports PostgresIntegration.

Layout follows:
integrations/postgres/
  __init__.py      — re-export
  integration.py   — PostgresIntegration orchestration
  cursor.py        — Cursor execution wrapper, metadata extraction, sanitization
"""

from .integration import PostgresIntegration

__all__ = ["PostgresIntegration"]
