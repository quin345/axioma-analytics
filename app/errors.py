"""Errors shared by the data-access layers.

The dashboard has a single source of rows - the KQL endpoint - so there is a
single failure type: anything that stops a query from answering. Keeping it in
its own module lets `app.kql`, `app.cache` and `app.service` raise and catch the
same class without importing each other.
"""
from __future__ import annotations


class DataSourceError(RuntimeError):
    """The KQL endpoint or the cache could not answer a query."""
