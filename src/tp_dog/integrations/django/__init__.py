"""Django integration package — re-exports DjangoIntegration and TpDogMiddleware.

Layout follows:
integrations/django/
  __init__.py      — re-export
  integration.py   — DjangoIntegration orchestration
  request.py       — SERVER request waterfall span
  middleware.py    — load_middleware wrapper
  middleware_init.py — TpDogMiddleware for auto-init
  view.py          — view span
  template.py      — template span
"""
from .integration import DjangoIntegration
from .middleware_init import TpDogMiddleware

__all__ = ["DjangoIntegration", "TpDogMiddleware"]
