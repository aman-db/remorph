from pyspark.sql import DataFrame

from databricks.labs.remorph.reconcile.connectors.adapter import SourceAdapter
from databricks.labs.remorph.reconcile.recon_config import (
    DatabaseConfig,
    Schema,
    Tables,
    TransformRuleMapping,
)


class SnowflakeAdapter(SourceAdapter):
    def extract_databricks_schema(self, table_conf: Tables, table_name: str) -> list[Schema]:
        pass

    def get_column_list_with_transformation(
        self, table_conf: Tables, columns: list[str], layer: str
    ) -> list[TransformRuleMapping]:
        pass

    def extract_schema(self, database_conf: DatabaseConfig, table_conf: Tables) -> list[Schema]:
        pass

    def extract_data(self, table_conf: Tables, query: str) -> DataFrame:
        pass
