# Copyright (c) Recommenders contributors.
# Licensed under the MIT License.

import numpy as np

try:
    from pyspark.sql.window import Window
    from pyspark.sql import functions as F
    from pyspark.storagelevel import StorageLevel
except ImportError:
    pass  # skip this import if we are in pure python environment

from recommenders.utils.constants import (
    DEFAULT_ITEM_COL,
    DEFAULT_USER_COL,
    DEFAULT_TIMESTAMP_COL,
)
from recommenders.datasets.split_utils import (
    process_split_ratio,
    min_rating_filter_spark,
)


def spark_random_split(data, ratio=0.75, seed=42):
    """Spark random splitter.

    Randomly split the data into several splits.

    Args:
        data (pyspark.sql.DataFrame): Spark DataFrame to be split.
        ratio (float or list): Ratio for splitting data. If it is a single float number
            it splits data into two halves and the ratio argument indicates the ratio of
            training data set; if it is a list of float numbers, the splitter splits
            data into several portions corresponding to the split ratios. If a list
            is provided and the ratios are not summed to 1, they will be normalized.
        seed (int): Seed.

    Returns:
        list: Splits of the input data as pyspark.sql.DataFrame.
    """
    multi_split, ratio = process_split_ratio(ratio)

    if multi_split:
        return data.randomSplit(ratio, seed=seed)
    else:
        return data.randomSplit([ratio, 1 - ratio], seed=seed)


def _do_stratification_spark(
    data,
    ratio=0.75,
    min_rating=1,
    filter_by="user",
    is_partitioned=True,
    is_random=True,
    seed=42,
    col_user=DEFAULT_USER_COL,
    col_item=DEFAULT_ITEM_COL,
    col_timestamp=DEFAULT_TIMESTAMP_COL,
):
    """Helper function to perform stratified splits.

    This function splits data in a stratified manner. That is, the same values for the
    filter_by column are retained in each split, but the corresponding set of entries
    are divided according to the ratio provided.

    Args:
        data (pyspark.sql.DataFrame): Spark DataFrame to be split.
        ratio (float or list): Ratio for splitting data. If it is a single float number
            it splits data into two sets and the ratio argument indicates the ratio of
            training data set; if it is a list of float numbers, the splitter splits
            data into several portions corresponding to the split ratios. If a list is
            provided and the ratios are not summed to 1, they will be normalized.
        min_rating (int): minimum number of ratings for user or item.
        filter_by (str): either "user" or "item", depending on which of the two is to filter
            with min_rating.
        is_partitioned (bool): flag to partition data by filter_by column
        is_random (bool): flag to make split randomly or use timestamp column
        seed (int): Seed.
        col_user (str): column name of user IDs.
        col_item (str): column name of item IDs.
        col_timestamp (str): column name of timestamps.

    Args:

    Returns:
    """
    # A few preliminary checks.
    if filter_by not in ["user", "item"]:
        raise ValueError("filter_by should be either 'user' or 'item'.")

    if min_rating < 1:
        raise ValueError("min_rating should be integer and larger than or equal to 1.")

    if col_user not in data.columns:
        raise ValueError("Schema of data not valid. Missing User Col")

    if col_item not in data.columns:
        raise ValueError("Schema of data not valid. Missing Item Col")

    if not is_random:
        if col_timestamp not in data.columns:
            raise ValueError("Schema of data not valid. Missing Timestamp Col")

    if min_rating > 1:
        data = min_rating_filter_spark(
            data=data,
            min_rating=min_rating,
            filter_by=filter_by,
            col_user=col_user,
            col_item=col_item,
        )

    split_by = col_user if filter_by == "user" else col_item
    partition_by = split_by if is_partitioned else []

    col_random = "_random"
    if is_random:
        data = data.withColumn(col_random, F.rand(seed=seed))
        order_by = F.col(col_random)
    else:
        order_by = F.col(col_timestamp)

    window_count = Window.partitionBy(partition_by)
    window_spec = Window.partitionBy(partition_by).orderBy(order_by)

    data = (
        data.withColumn("_count", F.count(split_by).over(window_count))
        .withColumn("_rank", F.row_number().over(window_spec) / F.col("_count"))
        .drop("_count", col_random)
    )
    # Persist to avoid duplicate rows in splits caused by lazy evaluation
    data.persist(StorageLevel.MEMORY_AND_DISK_2).count()

    multi_split, ratio = process_split_ratio(ratio)
    ratio = ratio if multi_split else [ratio, 1 - ratio]

    splits = []
    prev_split = None
    for split in np.cumsum(ratio):
        condition = F.col("_rank") <= split
        if prev_split is not None:
            condition &= F.col("_rank") > prev_split
        splits.append(data.filter(condition).drop("_rank"))
        prev_split = split

    return splits


def spark_chrono_split(
    data,
    ratio=0.75,
    min_rating=1,
    filter_by="user",
    col_user=DEFAULT_USER_COL,
    col_item=DEFAULT_ITEM_COL,
    col_timestamp=DEFAULT_TIMESTAMP_COL,
    no_partition=False,
):
    """Spark chronological splitter.

    This function splits data in a chronological manner. That is, for each user / item, the
    split function takes proportions of ratings which is specified by the split ratio(s).
    The split is stratified.

    Args:
        data (pyspark.sql.DataFrame): Spark DataFrame to be split.
        ratio (float or list): Ratio for splitting data. If it is a single float number
            it splits data into two sets and the ratio argument indicates the ratio of
            training data set; if it is a list of float numbers, the splitter splits
            data into several portions corresponding to the split ratios. If a list is
            provided and the ratios are not summed to 1, they will be normalized.
        min_rating (int): minimum number of ratings for user or item.
        filter_by (str): either "user" or "item", depending on which of the two is to filter
            with min_rating.
        col_user (str): column name of user IDs.
        col_item (str): column name of item IDs.
        col_timestamp (str): column name of timestamps.
        no_partition (bool): set to enable more accurate and less efficient splitting.

    Returns:
        list: Splits of the input data as pyspark.sql.DataFrame.
    """

    return _do_stratification_spark(
        data=data,
        ratio=ratio,
        min_rating=min_rating,
        filter_by=filter_by,
        is_random=False,
        col_user=col_user,
        col_item=col_item,
        col_timestamp=col_timestamp,
    )


def spark_stratified_split(
    data,
    ratio=0.75,
    min_rating=1,
    filter_by="user",
    col_user=DEFAULT_USER_COL,
    col_item=DEFAULT_ITEM_COL,
    seed=42,
):
    """Spark stratified splitter.

    For each user / item, the split function takes proportions of ratings which is
    specified by the split ratio(s). The split is stratified.

    Args:
        data (pyspark.sql.DataFrame): Spark DataFrame to be split.
        ratio (float or list): Ratio for splitting data. If it is a single float number
            it splits data into two halves and the ratio argument indicates the ratio of
            training data set; if it is a list of float numbers, the splitter splits
            data into several portions corresponding to the split ratios. If a list is
            provided and the ratios are not summed to 1, they will be normalized.
            Earlier indexed splits will have earlier times
            (e.g. the latest time per user or item in split[0] <= the earliest time per user or item in split[1])
        seed (int): Seed.
        min_rating (int): minimum number of ratings for user or item.
        filter_by (str): either "user" or "item", depending on which of the two is to filter
            with min_rating.
        col_user (str): column name of user IDs.
        col_item (str): column name of item IDs.

    Returns:
        list: Splits of the input data as pyspark.sql.DataFrame.
    """
    return _do_stratification_spark(
        data=data,
        ratio=ratio,
        min_rating=min_rating,
        filter_by=filter_by,
        seed=seed,
        col_user=col_user,
        col_item=col_item,
    )


def spark_timestamp_split(
    data,
    ratio=0.75,
    col_user=DEFAULT_USER_COL,
    col_item=DEFAULT_ITEM_COL,
    col_timestamp=DEFAULT_TIMESTAMP_COL,
):
    """Spark timestamp based splitter.

    The splitter splits the data into sets by timestamps without stratification on either user or item.
    The ratios are applied on the timestamp column which is divided accordingly into several partitions.

    Args:
        data (pyspark.sql.DataFrame): Spark DataFrame to be split.
        ratio (float or list): Ratio for splitting data. If it is a single float number
            it splits data into two sets and the ratio argument indicates the ratio of
            training data set; if it is a list of float numbers, the splitter splits
            data into several portions corresponding to the split ratios. If a list is
            provided and the ratios are not summed to 1, they will be normalized.
            Earlier indexed splits will have earlier times
            (e.g. the latest time in split[0] <= the earliest time in split[1])
        col_user (str): column name of user IDs.
        col_item (str): column name of item IDs.
        col_timestamp (str): column name of timestamps. Float number represented in
        seconds since Epoch.

    Returns:
        list: Splits of the input data as pyspark.sql.DataFrame.
    """
    return _do_stratification_spark(
        data=data,
        ratio=ratio,
        is_random=False,
        is_partitioned=False,
        col_user=col_user,
        col_item=col_item,
        col_timestamp=col_timestamp,
    )


def spark_leave_one_out_split(data, col_name):
    """PySpark leave-one-out splitter.

    This function splits data into two sets: one set contains all but the last
    occurrence of each user/item, and the other set contains only the last
    occurrence of each user/item.

    Args:
        data (pyspark.sql.DataFrame): PySpark DataFrame to be split.
        col_name (str): Column name to group by.

    Returns:
        pyspark.sql.DataFrame, pyspark.sql.DataFrame: Two splits of the input data.
    """
    col_num = "row_num"
    # window_spec = Window.partitionBy(col_name).orderBy(F.monotonically_increasing_id())
    # df_with_row_num = data.withColumn(col_num, F.row_number().over(window_spec))

    # df_test = df_with_row_num.filter(
    #     df_with_row_num.row_num
    #     == df_with_row_num.select(col_num).agg({col_num: "max"}).first()[0]
    # )
    # df_train = df_with_row_num.filter(
    #     df_with_row_num.row_num
    #     != df_with_row_num.select(col_num).agg({col_num: "max"}).first()[0]
    # )

    # # Define a window spec to order data within each group
    # window_spec = Window.partitionBy(col_name).orderBy(F.monotonically_increasing_id())

    # # Add a row number to each group
    # data_with_row_num = data.withColumn("row_num", F.row_number().over(window_spec))

    # # Determine the max row number for each group
    # max_row_num = data_with_row_num.groupBy(col_name).agg(
    #     F.max("row_num").alias("max_row_num")
    # )

    # # Extract the test data (last occurrence for each group)
    # test_data = data_with_row_num.join(max_row_num, on=[col_name])
    # test_data = test_data.filter(F.col("row_num") == F.col("max_row_num")).drop(
    #     "row_num", "max_row_num"
    # )

    # # Extract the training data (all but the last occurrence for each group)
    # train_data = data_with_row_num.join(
    #     test_data.select("row_num").withColumnRenamed("row_num", "test_row"),
    #     on=(data_with_row_num["row_num"] == F.col("test_row")),
    #     how="left_anti",
    # )

    # # Drop the additional row_num column before returning
    # train_data = train_data.drop("row_num")
    # test_data = test_data.drop("row_num")

    # return train_data, test_data

    # Create a window spec to rank rows within each group
    window = Window.partitionBy(col_name).orderBy(F.monotonically_increasing_id())

    # Add row numbers within each group
    data_with_rank = data.withColumn("row_num", F.row_number().over(window))

    # Get the count for each group
    counts = data_with_rank.groupBy(col_name).agg(F.count("*").alias("group_count"))

    # Join the counts back to get last row indicator
    data_with_counts = data_with_rank.join(counts, on=col_name, how="left")

    # Split into train and test sets
    df_test = data_with_counts.filter(F.col("row_num") == F.col("group_count")).drop(
        "row_num", "group_count"
    )

    df_train = data_with_counts.filter(F.col("row_num") != F.col("group_count")).drop(
        "row_num", "group_count"
    )

    return df_train, df_test
