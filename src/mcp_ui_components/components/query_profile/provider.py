"""Query-profile provider.

Turns a SQL query into the compact analytic model the card widget renders. Like
the other SQL controls it parses with :mod:`.sql` (no execution) and falls back to
the bundled monthly-encounter example when no query is supplied.
"""

from __future__ import annotations

from ...shared.examples import (
    EXAMPLE_JOIN_NL,
    EXAMPLE_JOIN_SCAN,
    EXAMPLE_JOIN_SQL,
    EXAMPLE_JOIN_TITLE,
)
from .sql import profile_query


class QueryProfileProvider:
    def query_profile(
        self,
        sql: str | None = None,
        nl: str | None = None,
        title: str | None = None,
        dialect: str = "bigquery",
        scan: str | None = None,
    ) -> dict:
        if not sql or not sql.strip():
            return profile_query(
                EXAMPLE_JOIN_SQL,
                nl=EXAMPLE_JOIN_NL,
                title=EXAMPLE_JOIN_TITLE,
                scan=EXAMPLE_JOIN_SCAN,
            )
        return profile_query(
            sql, nl=nl, title=title, dialect=dialect, scan=scan,
        )
