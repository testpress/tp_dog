"""PostgresIntegration — traces Django DB queries, plus raw psycopg2 cursors.

Two independent seams, with different driver support:

* **Django path** (steps 1-2) hooks ``Connection.execute_wrappers`` and
  ``django.db.backends.utils.CursorWrapper``. These are Django-level seams, so
  this path is *driver-agnostic* and already traces psycopg3 when Django is
  configured with it (Django >= 4.1 supports psycopg 3 as its driver).
* **Raw driver path** (step 3) installs OTel's ``Psycopg2Instrumentor``, which
  covers psycopg2 only. psycopg 3 has no equivalent wired up here.

``is_installed`` deliberately probes only Django and psycopg2. Reporting psycopg3
as installed would claim support the raw path cannot deliver, and would report
the integration as instrumented while emitting no spans at all on a non-Django
host. The ``"psycopg"`` alias in the manager still resolves to this integration,
so ``enable=["psycopg"]`` keeps working for Django projects on the psycopg3
driver.
"""

import importlib
import logging

from tp_dog.integrations.base import BaseIntegration
from .cursor import traced_django_cursor_exec, tp_dog_django_db_execute_wrapper

logger = logging.getLogger("tp_dog.integrations.postgres")


class PostgresIntegration(BaseIntegration):
    """Deep SQL query tracing for PostgreSQL and Django database backends."""

    name = "postgres"

    def is_installed(self) -> bool:
        has_django_db = False
        try:
            importlib.import_module("django.db.backends.utils")
            has_django_db = True
        except ImportError:
            pass

        has_psycopg2 = False
        try:
            importlib.import_module("psycopg2")
            has_psycopg2 = True
        except ImportError:
            pass

        # psycopg (v3) is intentionally NOT probed: only the Django path traces
        # it, and probing it would report this integration as installed on a
        # non-Django host where it emits no spans. See the module docstring.
        return has_django_db or has_psycopg2

    def _apply_patch(self) -> None:
        from . import cursor as _cursor
        _cursor.set_config(self._config)

        # 1. Attach native execute_wrappers to active Django database connections
        try:
            from django.db import connections
            from django.db.backends.signals import connection_created

            for conn in connections.all():
                if hasattr(conn, "execute_wrappers") and tp_dog_django_db_execute_wrapper not in conn.execute_wrappers:
                    conn.execute_wrappers.append(tp_dog_django_db_execute_wrapper)

            # Also listen for new connections
            def _on_connection_created(sender, connection, **kwargs):
                if hasattr(connection, "execute_wrappers") and tp_dog_django_db_execute_wrapper not in connection.execute_wrappers:
                    connection.execute_wrappers.append(tp_dog_django_db_execute_wrapper)

            self._connection_created_handler = _on_connection_created
            connection_created.connect(_on_connection_created, weak=False)
        except Exception as exc:
            logger.debug("Django connections execute_wrapper patch skipped: %s", exc)

        # 2. Patch Django database wrappers (django.db.backends.utils.CursorWrapper) as fallback
        try:
            import django.db.backends.utils

            self.wrap(
                "django.db.backends.utils.CursorWrapper",
                "execute",
                lambda w, i, a, k: traced_django_cursor_exec(w, i, a, k, "execute"),
            )
            self.wrap(
                "django.db.backends.utils.CursorWrapper",
                "executemany",
                lambda w, i, a, k: traced_django_cursor_exec(w, i, a, k, "executemany"),
            )

            if hasattr(django.db.backends.utils, "CursorDebugWrapper"):
                self.wrap(
                    "django.db.backends.utils.CursorDebugWrapper",
                    "execute",
                    lambda w, i, a, k: traced_django_cursor_exec(w, i, a, k, "execute"),
                )
                self.wrap(
                    "django.db.backends.utils.CursorDebugWrapper",
                    "executemany",
                    lambda w, i, a, k: traced_django_cursor_exec(w, i, a, k, "executemany"),
                )
        except Exception as exc:
            logger.debug("Django db backends utils patch skipped: %s", exc)

        # 3. Instrument direct psycopg2 driver via official OTel Psycopg2Instrumentor (for non-Django usage)
        has_django_db = False
        try:
            importlib.import_module("django.db.backends.utils")
            has_django_db = True
        except ImportError:
            pass

        if not has_django_db:
            try:
                from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

                instrumentor = Psycopg2Instrumentor()
                if not instrumentor.is_instrumented_by_opentelemetry:
                    instrumentor.instrument()
            except Exception as exc:
                logger.debug("Psycopg2Instrumentor patch skipped: %s", exc)

    def uninstrument(self) -> bool:
        # Remove connection signal handler and execute_wrappers
        try:
            from django.db import connections
            from django.db.backends.signals import connection_created

            if hasattr(self, "_connection_created_handler"):
                connection_created.disconnect(self._connection_created_handler)

            for conn in connections.all():
                if hasattr(conn, "execute_wrappers") and tp_dog_django_db_execute_wrapper in conn.execute_wrappers:
                    conn.execute_wrappers.remove(tp_dog_django_db_execute_wrapper)
        except Exception:
            pass

        try:
            from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

            instrumentor = Psycopg2Instrumentor()
            if instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.uninstrument()
        except Exception:
            pass

        return super().uninstrument()
