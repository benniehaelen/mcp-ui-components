"""Join-diagram provider.

Turns a SQL query into the join-diagram model the widget renders. Like the lineage
provider this is the only thing that touches the query: a real implementation might
also issue a BigQuery dry-run for the scanned-bytes figure. Here it parses the SQL
with :mod:`.sql` and falls back to the bundled monthly-encounter example when no
query is supplied.
"""

from __future__ import annotations

from ...shared.examples import (
    EXAMPLE_JOIN_NL,
    EXAMPLE_JOIN_SCAN,
    EXAMPLE_JOIN_SQL,
    EXAMPLE_JOIN_TITLE,
)
from .sql import parse_join_diagram


class JoinDiagramProvider:
    def join_diagram(
        self,
        sql: str | None = None,
        nl: str | None = None,
        title: str | None = None,
        dialect: str = "bigquery",
        scan: str | None = None,
    ) -> dict:
        if not sql or not sql.strip():
            return parse_join_diagram(
                EXAMPLE_JOIN_SQL,
                nl=EXAMPLE_JOIN_NL,
                title=EXAMPLE_JOIN_TITLE,
                scan=EXAMPLE_JOIN_SCAN,
            )
        return parse_join_diagram(
            sql, nl=nl, title=title, dialect=dialect, scan=scan,
        )
