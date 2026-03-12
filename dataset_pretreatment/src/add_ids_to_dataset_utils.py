import os
# 数据处理与科学计算
import numpy as np
import pandas as pd

def data_quality_summary(df: pd.DataFrame, output_path: str):
    """
    生成列缺失和行缺失的汇总报告，输出到同一个Excel文件的两个sheet
    
    Parameters:
    df: DataFrame - 输入数据框
    output_path: str - 输出Excel文件路径
    """
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # 列缺失汇总
        total_rows = len(df)
        miss_count = df.isnull().sum()
        miss_pct = miss_count / total_rows if total_rows else 0
        dtype = df.dtypes
        col_summary = pd.DataFrame({
            "column": df.columns,
            "dtype": dtype.values,
            "missing_count": miss_count.values,
            "missing_pct": miss_pct.values,
            "total_rows": total_rows
        })
        col_summary = col_summary.sort_values("missing_pct", ascending=False).reset_index(drop=True)
        
        # 行缺失汇总
        total_cols = df.shape[1]
        r_miss = df.isnull().sum(axis=1)
        r_pct = r_miss / total_cols if total_cols else 0
        row_summary = pd.DataFrame({
            "index": df.index,
            "missing_count": r_miss,
            "missing_pct": r_pct,
            "total_cols": total_cols
        })
        row_summary = row_summary.sort_values(["missing_count", "missing_pct"], ascending=False).reset_index(drop=True)
        
        # 写入Excel的不同sheet
        col_summary.to_excel(writer, sheet_name='Column_Missing_Summary', index=False)
        row_summary.to_excel(writer, sheet_name='Row_Missing_Summary', index=False)

def add_ids_to_dataset(dataset_df, ids_map_table, component_columns=None, key_column=None, target_columns=None):
    # component_columns=None: 需要添加ID数据的成分列列表，这些列存在于dataset_df中，其值用于在映射表中查找匹配项
    # key_column=None: 映射表中的键列，用于构建查找键，该列存在于ids_map_table中（可以是字符串或列表）
    # target_columns=None: 要从映射表中提取的目标列列表，这些列存在于ids_map_table中，将被添加到dataset_df中
    
    # 处理key_column参数：如果是列表，取第一个元素；如果是字符串，直接使用
    if key_column is None:
        key_column = 'compound'
    elif isinstance(key_column, list):
        if len(key_column) > 0:
            key_column = key_column[0]  # 取列表的第一个元素
        else:
            raise ValueError("key_column list cannot be empty")
    
    # 确保target_columns是列表
    if target_columns is None:
        # 如果未指定目标列，则使用所有非键列
        available_columns = [col for col in ids_map_table.columns if col != key_column]
    else:
        if isinstance(target_columns, str):
            target_columns = [target_columns]
        # 验证目标列在映射表中存在
        missing_cols = set(target_columns) - set(ids_map_table.columns)
        if missing_cols:
            raise ValueError(f"Target columns not found in ids_map_table: {list(missing_cols)}")
        # 确保目标列不包含键列
        if key_column in target_columns:
            raise ValueError(f"Target columns cannot include key column: {key_column}")
        available_columns = [col for col in target_columns if col in ids_map_table.columns]
    
    # 构建映射字典
    ids_map_dic = {}
    
    for _, row in ids_map_table.iterrows(): # "下划线“ 是行索引（通常是数字0,1,2...或自定义索引）
        # 直接使用单个键列的值作为键           # "row" 包含当前行的所有数据,是一个Pandas Series对象,可以通过列名（字段名）访问数据
        lookup_key = row[key_column]
        
        # 只提取指定的目标列
        ids = {
            col: row[col] 
            for col in available_columns  # 只使用目标列
        }
        
        ids_map_dic[lookup_key] = ids
    
    def _lookup_ids_by_key(key_val):  
        # 检查是否包含空值
        if pd.isna(key_val):
            return {}
        
        return ids_map_dic.get(key_val, {})
    
    # 为每个成分列提取ID数据
    for comp_col in component_columns:
        if comp_col not in dataset_df.columns:
            raise ValueError(f"Component column '{comp_col}' not found in dataset")
        
        comp_ids = dataset_df.apply(lambda row: _lookup_ids_by_key(row[comp_col]), axis=1)
        
        # 将字典展开为列
        for col_name in available_columns:
            new_col_name = f'{comp_col}_{col_name}'
            dataset_df[new_col_name] = comp_ids.apply(lambda x: x.get(col_name) if x else pd.NA)
    
    return dataset_df

