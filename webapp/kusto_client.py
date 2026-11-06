from datetime import datetime

from azure.identity import DefaultAzureCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder


class KustoReader:
    def __init__(self, cfg):
        self._database = cfg.database
        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
            cfg.cluster_uri,
            DefaultAzureCredential(),
        )
        self._client = KustoClient(kcsb)

    def run(self, query):
        response = self._client.execute(self._database, query)
        table = response.primary_results[0]
        columns = [c.column_name for c in table.columns]
        return [
            {name: _plain(value) for name, value in zip(columns, row)}
            for row in table
        ]


def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value
