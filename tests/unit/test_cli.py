import datetime
import io
from unittest.mock import create_autospec, patch

import pytest
import yaml

from databricks.labs.blueprint.tui import MockPrompts
from databricks.labs.remorph import cli
from databricks.labs.remorph.config import MorphConfig
from databricks.labs.remorph.helpers.recon_config_utils import ReconConfigPrompts
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.labs.blueprint.installation import MockInstallation

from databricks.labs.remorph.helpers.transpile_utils import TranspileUtils


@pytest.fixture
def mock_workspace_client_cli():
    state = {
        "/Users/foo/.remorph/config.yml": yaml.dump(
            {
                'version': 1,
                'catalog_name': 'transpiler',
                'schema_name': 'remorph',
                'source': 'snowflake',
                'sdk_config': {'cluster_id': 'test_cluster'},
            }
        ),
        "/Users/foo/.remorph/recon_config.yml": yaml.dump(
            {
                'version': 1,
                'source_schema': "src_schema",
                'target_catalog': "src_catalog",
                'target_schema': "tgt_schema",
                'tables': [
                    {
                        "source_name": 'src_table',
                        "target_name": 'tgt_table',
                        "join_columns": ['id'],
                        "jdbc_reader_options": None,
                        "select_columns": None,
                        "drop_columns": None,
                        "column_mapping": None,
                        "transformations": None,
                        "thresholds": None,
                        "filters": None,
                    }
                ],
                'source_catalog': "src_catalog",
            }
        ),
    }

    def download(path: str) -> io.StringIO | io.BytesIO:
        if path not in state:
            raise NotFound(path)
        if ".csv" in path:
            return io.BytesIO(state[path].encode('utf-8'))
        return io.StringIO(state[path])

    workspace_client = create_autospec(WorkspaceClient)
    workspace_client.current_user.me().user_name = "foo"
    workspace_client.workspace.download = download
    workspace_client.config = None
    return workspace_client


@pytest.fixture
def mock_installation_reconcile():
    return MockInstallation(
        {
            "reconcile.yml": {
                "data_source": "snowflake",
                "database_config": {
                    "source_catalog": "sf_test",
                    "source_schema": "mock",
                    "target_catalog": "hive_metastore",
                    "target_schema": "default",
                },
                "report_type": "include",
                "secret_scope": "remorph_snowflake",
                "tables": {
                    "filter_type": "include",
                    "tables_list": ["product"],
                },
                "metadata_config": {
                    "catalog": "remorph",
                    "schema": "reconcile",
                    "volume": "reconcile_volume",
                },
                "job_id": "54321",
                "version": 1,
            },
            "recon_config_snowflake_sf_test_include.json": {
                "source_catalog": "sf_functions_test",
                "source_schema": "mock",
                "tables": [
                    {
                        "column_mapping": [
                            {"source_name": "p_id", "target_name": "product_id"},
                            {"source_name": "p_name", "target_name": "product_name"},
                        ],
                        "drop_columns": None,
                        "filters": None,
                        "jdbc_reader_options": None,
                        "join_columns": ["p_id"],
                        "select_columns": ["p_id", "p_name"],
                        "source_name": "product",
                        "target_name": "product_delta",
                        "thresholds": None,
                        "transformations": [
                            {
                                "column_name": "creation_date",
                                "source": "creation_date",
                                "target": "to_date(creation_date,'yyyy-mm-dd')",
                            }
                        ],
                    }
                ],
                "target_catalog": "hive_metastore",
                "target_schema": "default",
            },
        }
    )


@pytest.fixture
def mock_transpile_invalid():
    return MockInstallation(
        {
            "config.yml": {
                'version': 1,
                'input_sql': '/path/to/sql/file.sql',
                'output_folder': '/path/to/output',
                'skip_validation': 'invalid_value',
                'catalog_name': 'my_catalog',
                'schema_name': 'my_schema',
                'source': 'invalid_dialect',
                'sdk_config': {'cluster_id': 'test_cluster'},
                'mode': 'current',
            }
        }
    )


@pytest.fixture
def temp_dirs_for_lineage(tmpdir):
    input_dir = tmpdir.mkdir("input")
    output_dir = tmpdir.mkdir("output")

    sample_sql_file = input_dir.join("sample.sql")
    sample_sql_content = """
    create table table1 select * from table2 inner join
    table3 on table2.id = table3.id where table2.id in (select id from table4);
    create table table2 select * from table4;
    create table table5 select * from table3 join table4 on table3.id = table4.id;
    """
    sample_sql_file.write(sample_sql_content)

    return input_dir, output_dir


def test_transpile_with_invalid_dialect(mock_workspace_client, mock_transpile_invalid):
    prompts = MockPrompts(
        {
            r"Would you like to overwrite workspace `Transpile Config` values": "no",
        }
    )

    with pytest.raises(Exception, match="Error: Invalid value for '--source'"):
        utils = TranspileUtils(mock_workspace_client, mock_transpile_invalid, prompts)
        utils.run()


def test_invalid_input_sql(mock_workspace_client_cli, mock_transpile_invalid):
    prompts = MockPrompts(
        {
            r"Would you like to overwrite workspace `Transpile Config` values": "no",
        }
    )
    invalid_input_path = MockInstallation(
        {
            "config.yml": {
                'version': 1,
                'input_sql': '/path/to/invalid/sql/file.sql',
                'output_folder': '/path/to/output',
                'skip_validation': True,
                'catalog_name': 'my_catalog',
                'schema_name': 'my_schema',
                'source': 'snowflake',
                'sdk_config': {'cluster_id': 'test_cluster'},
                'mode': 'current',
            }
        }
    )
    with (
        patch("os.path.exists", return_value=False),
        pytest.raises(Exception, match="Error: Invalid value for '--input_sql'"),
    ):
        utils = TranspileUtils(mock_workspace_client_cli, invalid_input_path, prompts)
        utils.run()


def test_transpile_with_valid_input(mock_workspace_client_cli):
    source = "snowflake"
    input_sql = "/path/to/sql/file.sql"
    output_folder = "/path/to/output"
    skip_validation = True
    catalog_name = "my_catalog"
    schema_name = "my_schema"
    mode = "current"
    sdk_config = {'cluster_id': 'test_cluster'}

    prompts = MockPrompts(
        {
            r"Would you like to overwrite workspace `Transpile Config` values": "no",
        }
    )
    invalid_input_path = MockInstallation(
        {
            "config.yml": {
                'version': 1,
                'input_sql': input_sql,
                'output_folder': output_folder,
                'skip_validation': skip_validation,
                'catalog_name': catalog_name,
                'schema_name': schema_name,
                'source': source,
                'sdk_config': sdk_config,
                'mode': mode,
            }
        }
    )
    with (
        patch("os.path.exists", return_value=True),
        patch("databricks.labs.remorph.helpers.transpile_utils.morph", return_value={}) as mock_morph,
    ):
        utils = TranspileUtils(mock_workspace_client_cli, invalid_input_path, prompts)
        utils.run()

        mock_morph.assert_called_once_with(
            mock_workspace_client_cli,
            MorphConfig(
                sdk_config=sdk_config,
                source=source,
                input_sql=input_sql,
                output_folder=output_folder,
                skip_validation=skip_validation,
                catalog_name=catalog_name,
                schema_name=schema_name,
                mode=mode,
            ),
        )


def test_transpile_empty_output_folder(mock_workspace_client_cli):
    source = "snowflake"
    input_sql = "/path/to/sql/file2.sql"
    output_folder = ""
    skip_validation = False
    catalog_name = "my_catalog"
    schema_name = "my_schema"

    mode = "current"
    sdk_config = {'cluster_id': 'test_cluster'}

    prompts = MockPrompts(
        {
            r"Would you like to overwrite workspace `Transpile Config` values": "no",
        }
    )
    invalid_output_folder = MockInstallation(
        {
            "config.yml": {
                'version': 1,
                'input_sql': input_sql,
                'output_folder': output_folder,
                'skip_validation': skip_validation,
                'catalog_name': catalog_name,
                'schema_name': schema_name,
                'source': source,
                'sdk_config': sdk_config,
                'mode': mode,
            }
        }
    )

    with (
        patch("os.path.exists", return_value=True),
        patch("databricks.labs.remorph.helpers.transpile_utils.morph", return_value={}) as mock_morph,
    ):
        utils = TranspileUtils(mock_workspace_client_cli, invalid_output_folder, prompts)
        utils.run()

        mock_morph.assert_called_once_with(
            mock_workspace_client_cli,
            MorphConfig(
                sdk_config=sdk_config,
                source=source,
                input_sql=input_sql,
                output_folder=None,
                skip_validation=False,
                catalog_name=catalog_name,
                schema_name=schema_name,
                mode=mode,
            ),
        )


def test_transpile_with_incorrect_input_mode(mock_workspace_client_cli):
    prompts = MockPrompts(
        {
            r"Would you like to overwrite workspace `Transpile Config` values": "no",
        }
    )
    invalid_mode = MockInstallation(
        {
            "config.yml": {
                'version': 1,
                'input_sql': "/path/to/sql/file2.sql",
                'output_folder': "",
                'skip_validation': False,
                'catalog_name': "my_catalog",
                'schema_name': "my_schema",
                'source': "snowflake",
                'mode': "preview",
            }
        }
    )

    with (
        patch("os.path.exists", return_value=True),
        pytest.raises(Exception, match="Error: Invalid value for '--mode':"),
    ):
        utils = TranspileUtils(mock_workspace_client_cli, invalid_mode, prompts)
        utils.run()


def test_transpile_with_incorrect_input_source(mock_workspace_client_cli):
    prompts = MockPrompts(
        {
            r"Would you like to overwrite workspace `Transpile Config` values": "no",
        }
    )
    invalid_input_source = MockInstallation(
        {
            "config.yml": {
                'version': 1,
                'input_sql': "/path/to/sql/file2.sql",
                'output_folder': "",
                'skip_validation': False,
                'catalog_name': "my_catalog",
                'schema_name': "my_schema",
                'source': "postgres",
                'mode': "preview",
            }
        }
    )

    with (
        patch("os.path.exists", return_value=True),
        pytest.raises(Exception, match="Error: Invalid value for '--source':"),
    ):
        utils = TranspileUtils(mock_workspace_client_cli, invalid_input_source, prompts)
        utils.run()


def test_invalid_transpile_config(mock_workspace_client_cli, mock_transpile_invalid):

    invalid_config = MockInstallation(
        {
            "config.yml": {
                'version': 1,
            }
        }
    )
    with pytest.raises(AssertionError, match="Error: Cannot load Transpile `config.yml` from Databricks Workspace"):
        utils = TranspileUtils(mock_workspace_client_cli, invalid_config, MockPrompts({}))
        utils.run()


def test_missing_transpile_config(mock_workspace_client_cli, mock_transpile_invalid):

    missing_config = MockInstallation({})
    with pytest.raises(AssertionError, match="Error: Cannot load Transpile `config.yml` from Databricks Workspace"):
        utils = TranspileUtils(mock_workspace_client_cli, missing_config, MockPrompts({}))
        utils.run()


def test_cli_transpile(mock_workspace_client, mock_transpile_invalid):
    with patch(
        "databricks.labs.blueprint.installation.Installation.assume_user_home", return_value=mock_transpile_invalid
    ):
        with patch("databricks.labs.remorph.helpers.transpile_utils.TranspileUtils.run", return_value=None):
            assert cli.transpile(mock_workspace_client) is None


def test_cli_transpile_overwrite_workspace_values(mock_workspace_client, mock_transpile_invalid):
    prompts = MockPrompts(
        {
            r"Would you like to overwrite workspace `Transpile Config` values": "yes",
        }
    )
    utils = TranspileUtils(mock_workspace_client, mock_transpile_invalid, prompts)
    with pytest.raises(AssertionError, match="Error: Cannot load Transpile `config.yml` from Databricks Workspace"):
        utils.run()


def test_generate_lineage_valid_input(temp_dirs_for_lineage, mock_workspace_client_cli):
    input_dir, output_dir = temp_dirs_for_lineage
    cli.generate_lineage(
        mock_workspace_client_cli, source="snowflake", input_sql=str(input_dir), output_folder=str(output_dir)
    )

    date_str = datetime.datetime.now().strftime("%d%m%y")
    output_filename = f"lineage_{date_str}.dot"
    output_file = output_dir.join(output_filename)
    assert output_file.check(file=1)
    expected_output = """
    flowchart TD
    Table1 --> Table2
    Table1 --> Table3
    Table1 --> Table4
    Table2 --> Table4
    Table3
    Table4
    Table5 --> Table3
    Table5 --> Table4
    """
    actual_output = output_file.read()
    assert actual_output.strip() == expected_output.strip()


def test_generate_lineage_with_invalid_dialect(mock_workspace_client_cli):
    with pytest.raises(Exception, match="Error: Invalid value for '--source'"):
        cli.generate_lineage(
            mock_workspace_client_cli,
            source="invalid_dialect",
            input_sql="/path/to/sql/file.sql",
            output_folder="/path/to/output",
        )


def test_generate_lineage_invalid_input_sql(mock_workspace_client_cli):
    with (
        patch("os.path.exists", return_value=False),
        pytest.raises(Exception, match="Error: Invalid value for '--input_sql'"),
    ):
        cli.generate_lineage(
            mock_workspace_client_cli,
            source="snowflake",
            input_sql="/path/to/invalid/sql/file.sql",
            output_folder="/path/to/output",
        )


def test_configure_secrets_databricks(mock_workspace_client):
    source_dict = {"databricks": "0", "netezza": "1", "oracle": "2", "snowflake": "3"}
    prompts = MockPrompts(
        {
            r"Select the source": source_dict["databricks"],
        }
    )

    recon_conf = ReconConfigPrompts(mock_workspace_client, prompts)
    recon_conf.prompt_source()

    recon_conf.prompt_and_save_connection_details()


def test_cli_configure_secrets_config(mock_workspace_client):
    with patch("databricks.labs.remorph.cli.ReconConfigPrompts") as mock_recon_config:
        cli.configure_secrets(mock_workspace_client)
        mock_recon_config.assert_called_once_with(mock_workspace_client)


def test_cli_reconcile(mock_workspace_client, mock_installation_reconcile, monkeypatch):
    with patch(
        "databricks.labs.blueprint.installation.Installation.assume_user_home", return_value=mock_installation_reconcile
    ):
        with patch("databricks.labs.remorph.helpers.reconcile_utils.ReconcileUtils.run", return_value=True):
            cli.reconcile(mock_workspace_client)
