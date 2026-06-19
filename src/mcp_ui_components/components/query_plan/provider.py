"""Query-plan provider.

The sibling of the join-diagram provider: where that one extracts just the join
graph, this decomposes the whole statement into its logical execution pipeline
(:func:`.sql.parse_query_plan`). Falls back to the bundled monthly-encounter
example when no query is supplied.
"""

from __future__ import annotations

from ...shared.examples import (
    EXAMPLE_JOIN_NL,
    EXAMPLE_JOIN_SCAN,
    EXAMPLE_JOIN_SQL,
    EXAMPLE_JOIN_TITLE,
)
from .sql import parse_query_plan


class QueryPlanProvider:
    def query_plan(
        self,
        sql: str | None = None,
        nl: str | None = None,
        title: str | None = None,
        dialect: str = "bigquery",
        scan: str | None = None,
    ) -> dict:
        if not sql or not sql.strip():
            return parse_query_plan(
                EXAMPLE_JOIN_SQL,
                nl=EXAMPLE_JOIN_NL,
                title=EXAMPLE_JOIN_TITLE,
                scan=EXAMPLE_JOIN_SCAN,
            )
        return parse_query_plan(
            sql, nl=nl, title=title, dialect=dialect, scan=scan,
        )
