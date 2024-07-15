import logging
from functools import reduce

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col, expr, lit

from databricks.labs.remorph.reconcile.exception import ColumnMismatchException
from databricks.labs.remorph.reconcile.recon_capture import (
    ReconIntermediatePersist,
)
from databricks.labs.remorph.reconcile.recon_config import (
    DataReconcileOutput,
    MismatchOutput,
    Aggregate,
)

logger = logging.getLogger(__name__)

_HASH_COLUMN_NAME = "hash_value_recon"
_SAMPLE_ROWS = 50


def raise_column_mismatch_exception(msg: str, source_missing: list[str], target_missing: list[str]) -> Exception:
    error_msg = (
        f"{msg}\n"
        f"columns missing in source: {','.join(source_missing) if source_missing else None}\n"
        f"columns missing in target: {','.join(target_missing) if target_missing else None}\n"
    )
    return ColumnMismatchException(error_msg)


def _generate_join_condition(source_alias, target_alias, key_columns):
    conditions = [
        col(f"{source_alias}.{key_column}").eqNullSafe(col(f"{target_alias}.{key_column}"))
        for key_column in key_columns
    ]
    return reduce(lambda a, b: a & b, conditions)


def _generate_agg_join_condition(source_alias: str, target_alias: str, join_columns: list[tuple[str, str]]):
    conditions = [
        col(f"{source_alias}.{item[0]}").eqNullSafe(col(f"{target_alias}.{item[1]}")) for item in join_columns
    ]
    return reduce(lambda a, b: a & b, conditions)


def reconcile_data(
    source: DataFrame,
    target: DataFrame,
    key_columns: list[str],
    report_type: str,
    spark: SparkSession,
    path: str,
) -> DataReconcileOutput:
    source_alias = "src"
    target_alias = "tgt"
    if report_type not in {"data", "all"}:
        key_columns = [_HASH_COLUMN_NAME]
    df = (
        source.alias(source_alias)
        .join(
            other=target.alias(target_alias),
            on=_generate_join_condition(source_alias, target_alias, key_columns),
            how="full",
        )
        .selectExpr(
            *[f'{source_alias}.{col_name} as {source_alias}_{col_name}' for col_name in source.columns],
            *[f'{target_alias}.{col_name} as {target_alias}_{col_name}' for col_name in target.columns],
        )
    )

    # Write unmatched df to volume
    df = ReconIntermediatePersist(spark, path).write_and_read_unmatched_df_with_volumes(df)
    logger.warning(f"Unmatched data is written to {path} successfully")

    mismatch = _get_mismatch_data(df, source_alias, target_alias) if report_type in {"all", "data"} else None

    missing_in_src = (
        df.filter(col(f"{source_alias}_{_HASH_COLUMN_NAME}").isNull())
        .select(
            *[
                col(col_name).alias(col_name.replace(f'{target_alias}_', '').lower())
                for col_name in df.columns
                if col_name.startswith(f'{target_alias}_')
            ]
        )
        .drop(_HASH_COLUMN_NAME)
    )

    missing_in_tgt = (
        df.filter(col(f"{target_alias}_{_HASH_COLUMN_NAME}").isNull())
        .select(
            *[
                col(col_name).alias(col_name.replace(f'{source_alias}_', '').lower())
                for col_name in df.columns
                if col_name.startswith(f'{source_alias}_')
            ]
        )
        .drop(_HASH_COLUMN_NAME)
    )
    mismatch_count = 0
    if mismatch:
        mismatch_count = mismatch.count()

    missing_in_src_count = missing_in_src.count()
    missing_in_tgt_count = missing_in_tgt.count()

    return DataReconcileOutput(
        mismatch_count=mismatch_count,
        missing_in_src_count=missing_in_src_count,
        missing_in_tgt_count=missing_in_tgt_count,
        missing_in_src=missing_in_src.limit(_SAMPLE_ROWS),
        missing_in_tgt=missing_in_tgt.limit(_SAMPLE_ROWS),
        mismatch=MismatchOutput(mismatch_df=mismatch),
    )


def reconcile_agg_data(
    source: DataFrame,
    target: DataFrame,
    group_list: list[Aggregate],
) -> DataReconcileOutput:
    source_alias = "src"
    target_alias = "tgt"

    select_columns = [
        (f"source_{agg.type}_{agg_col}".lower(), f"target_{agg.type}_{agg_col}".lower())
        for agg in group_list
        for agg_col in agg.agg_cols
    ]

    group_columns = None
    df = source.alias(source_alias).join(
        other=target.alias(target_alias),
        how="cross",
    )

    if group_list[0].group_by_cols:
        group_columns = [
            (f"source_group_by_{group_col}", f"target_group_by_{group_col}")
            for group_col in group_list[0].group_by_cols
        ]

        select_columns = select_columns + group_columns
        df = source.alias(source_alias).join(
            other=target.alias(target_alias),
            on=_generate_agg_join_condition(source_alias, target_alias, group_columns),
            how="full",
        )

    df = df.selectExpr(
        *source.columns,
        *target.columns,
    ).cache()

    df.show()

    # Write unmatched df to volume
    # volume_df = ReconIntermediatePersist(spark, path + f"/{group_key}").write_and_read_unmatched_df_with_volumes(df)
    # logger.warning(f"Unmatched data is written to {path} successfully")

    # volume_df.show()

    mismatch = _get_mismatch_agg_data(df, select_columns, group_columns)

    mismatch.show()

    missing_in_src = df.filter(_agg_conditions(select_columns, "missing_in_src")).select(*target.columns)
    missing_in_src.show()

    missing_in_tgt = df.filter(_agg_conditions(select_columns, "missing_in_tgt")).select(*source.columns)
    missing_in_tgt.show()

    mismatch_count = 0
    if mismatch:
        mismatch_count = mismatch.count()

    missing_in_src_count = missing_in_src.count()
    missing_in_tgt_count = missing_in_tgt.count()

    print(
        f"mismatch_count: {mismatch_count}",
        f"missing_in_src_count: {missing_in_src_count}",
        f"missing_in_tgt_count: {missing_in_tgt_count}",
    )

    return DataReconcileOutput(
        mismatch_count=mismatch_count,
        missing_in_src_count=missing_in_src_count,
        missing_in_tgt_count=missing_in_tgt_count,
        missing_in_src=missing_in_src.limit(_SAMPLE_ROWS),
        missing_in_tgt=missing_in_tgt.limit(_SAMPLE_ROWS),
        mismatch=MismatchOutput(mismatch_df=mismatch),
    )


def _get_mismatch_data(df: DataFrame, src_alias: str, tgt_alias: str) -> DataFrame:
    return (
        df.filter(
            (col(f"{src_alias}_{_HASH_COLUMN_NAME}").isNotNull())
            & (col(f"{tgt_alias}_{_HASH_COLUMN_NAME}").isNotNull())
        )
        .withColumn(
            "hash_match",
            col(f"{src_alias}_{_HASH_COLUMN_NAME}") == col(f"{tgt_alias}_{_HASH_COLUMN_NAME}"),
        )
        .filter(col("hash_match") == lit(False))
        .select(
            *[
                col(col_name).alias(col_name.replace(f'{src_alias}_', '').lower())
                for col_name in df.columns
                if col_name.startswith(f'{src_alias}_')
            ]
        )
        .drop(_HASH_COLUMN_NAME)
    )


def _agg_conditions(
    cols: list[tuple[str, str]] | None,
    condition_type: str = "group_filter",
    op_type: str = "and",
):
    assert cols, "Columns must be specified for aggregation conditions"

    # conditions_list = [f"({item[0]} is NULL AND {item[1]} is NULL)" for item in cols]
    conditions_list = [(col(f"{item[0]}").isNotNull() & col(f"{item[1]}").isNotNull()) for item in cols]

    match condition_type:
        case "select":
            # conditions_list = [f"({item[0]} == {item[1]})" for item in cols]
            conditions_list = [col(f"{item[0]}") == col(f"{item[1]}") for item in cols]
        case "missing_in_src":
            # conditions_list = [f"({item[0]} is NULL)" for item in cols]
            conditions_list = [col(f"{item[0]}").isNull() for item in cols]
        case "missing_in_tgt":
            # conditions_list = [f"({item[1]} is NULL)" for item in cols]
            conditions_list = [col(f"{item[1]}").isNull() for item in cols]

    # return " AND ".join(conditions_list) if op_type == "and" else " OR ".join(conditions_list)

    return reduce(lambda a, b: a & b if op_type == "and" else a | b, conditions_list)
    # if op_type == "or":
    #     return reduce(lambda a, b: a | b, conditions_list)
    #
    # return reduce(lambda a, b: a & b, conditions_list)


def _match_cols(select_cols: list[tuple[str, str]]):
    items = []
    for item in select_cols:
        match_col_name = item[0].replace("source_", "") + "_match"
        items.append((match_col_name, col(f"{item[0]}") == col(f"{item[1]}")))
    return items


def _get_mismatch_agg_data(
    df: DataFrame,
    select_cols: list[tuple[str, str]],
    group_cols: list[tuple[str, str]] | None,
) -> DataFrame:
    if group_cols:
        filter_conditions = _agg_conditions(group_cols)
        df = df.filter(filter_conditions)

    select_conditions = _agg_conditions(select_cols, "select")

    match_cols_dict = _match_cols(select_cols)

    df_with_match_cols = df
    for match_col in match_cols_dict:
        df_with_match_cols = df_with_match_cols.withColumn(match_col[0], match_col[1])

    return df_with_match_cols.withColumn("agg_data_match", select_conditions).filter(
        col("agg_data_match") == lit(False)
    )


def capture_mismatch_data_and_columns(source: DataFrame, target: DataFrame, key_columns: list[str]) -> MismatchOutput:
    source_columns = source.columns
    target_columns = target.columns

    if source_columns != target_columns:
        message = "source and target should have same columns for capturing the mismatch data"
        source_missing = [column for column in target_columns if column not in source_columns]
        target_missing = [column for column in source_columns if column not in target_columns]
        raise raise_column_mismatch_exception(message, source_missing, target_missing)

    check_columns = [column for column in source_columns if column not in key_columns]
    mismatch_df = _get_mismatch_df(source, target, key_columns, check_columns)
    mismatch_columns = _get_mismatch_columns(mismatch_df, check_columns)
    return MismatchOutput(mismatch_df, mismatch_columns)


def _get_mismatch_columns(df: DataFrame, columns: list[str]):
    # Collect the DataFrame to a local variable
    local_df = df.collect()
    mismatch_columns = []
    for column in columns:
        # Check if any row has False in the column
        if any(not row[column + "_match"] for row in local_df):
            mismatch_columns.append(column)
    return mismatch_columns


def _get_mismatch_df(source: DataFrame, target: DataFrame, key_columns: list[str], column_list: list[str]):
    source_aliased = [col('base.' + column).alias(column + '_base') for column in column_list]
    target_aliased = [col('compare.' + column).alias(column + '_compare') for column in column_list]

    match_expr = [expr(f"{column}_base=={column}_compare").alias(column + "_match") for column in column_list]
    key_cols = [col(column) for column in key_columns]
    select_expr = key_cols + source_aliased + target_aliased + match_expr

    filter_columns = " and ".join([column + "_match" for column in column_list])
    filter_expr = ~expr(filter_columns)

    mismatch_df = (
        source.alias('base')
        .join(other=target.alias('compare'), on=key_columns, how="inner")
        .select(*select_expr)
        .filter(filter_expr)
    )
    compare_columns = [column for column in mismatch_df.columns if column not in key_columns]
    return mismatch_df.select(*key_columns + sorted(compare_columns))


def alias_column_str(alias: str, columns: list[str]) -> list[str]:
    return [f"{alias}.{column}" for column in columns]
