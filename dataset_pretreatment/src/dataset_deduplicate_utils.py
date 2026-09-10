import pandas as pd
import numpy as np
import os
import re
from openpyxl import load_workbook
from openpyxl.styles import Alignment
import logging

logger = logging.getLogger(__name__)

def data_quality_summary(df, output_path):
    """
    生成列缺失和行缺失的汇总报告，输出到同一个Excel文件的两个sheet
    
    Parameters:
    df: DataFrame - 输入数据框
    output_path: str - 输出Excel文件路径
    """
    output_dir = os.path.dirname(output_path)
    if output_dir: 
        os.makedirs(output_dir, exist_ok=True)

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


def _clean_missing_data(df, essential_cols):
    """关键字段缺失检查与清理"""
    logger.info("\n=== step 1: 关键字段缺失处理 ===")
    missing_cols = [c for c in essential_cols if c not in df.columns]
    if missing_cols:
        logger.error(f"错误: 数据集缺少关键列，无法构成DES体系: {missing_cols}")
        return None

    null_mask = df[essential_cols].isnull().any(axis=1) 
    if null_mask.any():
        logger.warning(f"检测到 {null_mask.sum()} 个包含关键缺失值的记录，正在删除这些记录。")
        df = df.dropna(subset=essential_cols) # 检测 关键列中 是否存在空值，若有则直接删除 对应行，并记录删除数量。
    else:
        logger.info("未检测到在关键字段有缺失的记录。")
    logger.info(f"剩余记录数: {len(df)}")
    return df

def _filter_and_log_invalid_numerics(df, num_cols, state_var_cols, output_dir):

    logger.info("\n=== step 2: 异常数值审查与拦截 ===")

    if state_var_cols is None:
        state_vars = []
    elif isinstance(state_var_cols, str):
        state_vars = [state_var_cols]
    else:
        state_vars = list(state_var_cols)
        
    cols_to_check = num_cols + state_vars
    
    invalid_mask = pd.Series(False, index=df.index)
    for col in cols_to_check:
        if col not in df.columns: continue
        
        # 原本非空，但强转数字后变成 NaN 的，就是非法字符
        is_not_empty = df[col].notna() & (df[col].astype(str).str.strip() != "") # 找出真正写了东西的单元格
        is_invalid_num = pd.to_numeric(df[col], errors='coerce').isna() # 找出转不成数字的单元格
        invalid_mask |= (is_not_empty & is_invalid_num) # 把当前列的脏数据标记累积到总掩码中
        
    invalid_df = df[invalid_mask].copy()
    
    if not invalid_df.empty:
        logger.warning(f"⚠️拦截到 {len(invalid_df)} 行包含非法数值的数据！")
        log_path = os.path.join(output_dir, 'raw-ds_invalid_numerics_log.xlsx')
        invalid_df.to_excel(log_path, index=False)
        logger.info(f" ├─ 异常数据已记录并单独保存至: {log_path}")
        
        df = df[~invalid_mask].copy()
        logger.info(f" └─ 剔除后剩余有效行数: {len(df)}")
    else:
        logger.info(" └─ 未发现非法数值")
        
    return df

def _generate_invariant_keys(df, str_cols, num_cols, state_var_cols=None):
    
    if state_var_cols is None:
        state_vars = []
    elif isinstance(state_var_cols, str):
        state_vars = [state_var_cols]
    else:
        state_vars = list(state_var_cols)

    if state_vars:
        logger.info(f"\n=== step3: 构建数值去重键 (含状态变量: {state_vars}) ===")
        cols_to_numeric = num_cols + state_vars
    else:
        logger.info("\n=== step3: 构建数值去重键 (仅摩尔分数) ===")
        cols_to_numeric = num_cols

    temp_nums = df[cols_to_numeric].apply(pd.to_numeric, errors='coerce').fillna(0)
    
    # 1. 向量化拼接 ci 和 xi列
    pair_df = pd.DataFrame(index=df.index, dtype=object)
    for s_col, n_col in zip(str_cols, num_cols):
        s_series = df[s_col].fillna("").astype(str).str.strip().str.lower()
        mask = (s_series != "") & (s_series != "nan")
        n_series = temp_nums[n_col].map('{:.4f}'.format)
        pair_df[s_col] = np.where(mask, s_series + "::" + n_series, None)
    
    # 2. 逐行按字符串排序并连接
    pair_matrix = pair_df.values
    comp_keys = []
    for row in pair_matrix:
        valid_pairs = [x for x in row if pd.notna(x)]
        valid_pairs.sort() 
        comp_keys.append("~~".join(valid_pairs))
        
    comp_keys_series = pd.Series(comp_keys, index=df.index)

    # 3. 动态挂载状态变量
    if state_vars:
        state_series_list = []
        for col in state_vars:
            formatted_state = col + "::" + temp_nums[col].map('{:.2f}'.format)
            state_series_list.append(formatted_state)
        
        state_suffix_series = state_series_list[0]
        for s in state_series_list[1:]:
            state_suffix_series = state_suffix_series + "~~" + s
            
        final_keys = np.where(
            comp_keys_series != "", 
            comp_keys_series + "~~" + state_suffix_series, 
            "~~" + state_suffix_series
        )
        return pd.Series(final_keys, index=df.index)
    else:
        return comp_keys_series
   

def _export_duplicate_details(duplicates_df, output_dir, ref_col):
    """生成Excel 留痕报表，并增加重复组容量统计"""
    if duplicates_df.empty:
        logger.info("未发现重复数据，跳过报表生成。")
        return

    # 1. 添加分组编号 和 组内记录数统计 (_group_size)
    duplicates_df['_dedup_number'] = duplicates_df.groupby('_dedup_key').ngroup() + 1
    duplicates_df['_group_size'] = duplicates_df.groupby('_dedup_key')['_dedup_key'].transform('size')
    
    # 2. 排序
    sorted_dups = duplicates_df.sort_values(by=['_dedup_number', ref_col])
    
    # 3. 调整列顺序
    cols = ['_dedup_number', '_group_size', '_dedup_key', 'original_excel_index'] + \
           [c for c in sorted_dups.columns if c not in ['_dedup_number', '_group_size', '_dedup_key', 'original_excel_index']]
    sorted_dups = sorted_dups[cols]
    
    detail_path = os.path.join(output_dir, 'raw-ds_dd_details.xlsx')
    sorted_dups.to_excel(detail_path, index=False) # 先落袋为安再打开优化
    
    # 4. Openpyxl 优化格式 这段都是AI写的
    try:
        wb = load_workbook(detail_path)
        ws = wb.active
        
        start_row, max_row = 2, ws.max_row
        prev_val = ws.cell(row=2, column=1).value # 依靠第1列(_dedup_number)来判断边界
        
        for row in range(3, max_row + 2):
            curr_val = ws.cell(row=row, column=1).value if row <= max_row else None
            if curr_val != prev_val:
                if row - 1 > start_row:
                    # 合并第1列 (_dedup_number)
                    ws.merge_cells(start_row=start_row, start_column=1, end_row=row-1, end_column=1)
                    ws.cell(row=start_row, column=1).alignment = Alignment(horizontal='center', vertical='center')
                    
                    # 合并第2列 (_group_size)
                    ws.merge_cells(start_row=start_row, start_column=2, end_row=row-1, end_column=2)
                    ws.cell(row=start_row, column=2).alignment = Alignment(horizontal='center', vertical='center')
                    
                start_row, prev_val = row, curr_val
                
        wb.save(detail_path)
        logger.info(f"重复数据详情已保存至: {detail_path}")
    except Exception as e:
        logger.warning(f"警告: 合并单元格操作失败，但数据已保存。错误: {e}")


def _fuse_duplicate_groups(duplicates_df, property_col, ref_col, agg_method='mean'):
    """
    子功能 4：执行物理化学约束下的数据融合
    支持的聚合策略 (agg_method): 'mean' (均值), 'median' (中位数), 'mode' (众数)
    """
    logger.info(f"\n=== 合并重复数据 (当前聚合策略: {agg_method}) ===")
    if duplicates_df.empty:
        return pd.DataFrame()

    # 防呆设计：如果传入了不支持的策略，强制切回均值
    valid_methods = ['mean', 'median', 'mode']
    if agg_method not in valid_methods:
        logger.warning(f"⚠️ 警告: 不支持的聚合方法 '{agg_method}'，已自动回退到 'mean'")
        agg_method = 'mean'

    processed_rows = []
    for key, group in duplicates_df.groupby('_dedup_key'):
        # 1. 处理参考文献合并 (去重并用分号拼接)
        refs = group[ref_col].dropna().astype(str).tolist()
        all_refs = []
        for r in refs:
            r_str = re.sub(r'\.0$', '', r.strip())
            all_refs.extend([x.strip() for x in r_str.split(';')])
        combined_ref = "; ".join(sorted(list(set(all_refs))))
        
        # 2. 清洗并提取当前组的所有有效属性值
        prop_values = pd.to_numeric(group[property_col], errors='coerce').dropna()
        
        # 3. 根据策略执行聚合计算
        if prop_values.empty:
            agg_val = np.nan
        elif agg_method == 'mean':
            agg_val = prop_values.mean()
        elif agg_method == 'median':
            agg_val = prop_values.median()
        elif agg_method == 'mode':
            modes = prop_values.mode()
            if len(modes) == len(prop_values) or len(modes) > 1:    
                agg_val = prop_values.median()
            else:
                agg_val = modes.iloc[0]
            
        # 4. 锁定配比模板 (保留第一行的所有其他元数据)
        new_row = group.iloc[0].copy()
        new_row[property_col] = agg_val
        new_row[ref_col] = combined_ref
        processed_rows.append(new_row)
        
    fused_df = pd.DataFrame(processed_rows)
    logger.info(f"已将 {len(duplicates_df)} 行重复数据压缩为 {len(fused_df)} 条唯一记录。")
    return fused_df


# =========================================================================
# 阶段一调度器：识别并导出重复详情 (Phase 1)
# =========================================================================
def identify_and_export_duplicates(original_df, essential_cols, str_cols, num_cols, output_dir, ref_col='ref', state_var_cols=None):
    """
    第一阶段：清洗数据、拦截非法值、生成置换不变性去重键，并导出重复详情供审查。
    返回带有 '_dedup_key' 和 'is_duplicate' 标识的中间态 DataFrame。
    """
    assert len(str_cols) == len(num_cols), "错误：组分名称列和数值列的长度必须一致！"
    if output_dir: os.makedirs(output_dir, exist_ok=True)

    data_df = original_df.copy()
    data_df['original_excel_index'] = data_df.index + 2

    # 1. 清理关键缺失数据
    data_df = _clean_missing_data(data_df, essential_cols)
    if data_df is None: return None

    # 2 拦截并记录含有非法数值的脏数据
    data_df = _filter_and_log_invalid_numerics(data_df, num_cols, state_var_cols, output_dir)
    if data_df.empty: return None

    # 2. 生成entry指纹（去重键）
    data_df['_dedup_key'] = _generate_invariant_keys(data_df, str_cols, num_cols, state_var_cols)
    
    #3. 过滤毫无意义的无效指纹
    empty_mask = data_df['_dedup_key'].str.startswith("~~") | (data_df['_dedup_key'] == "")
    if empty_mask.any():
        logger.warning(f"警告: 剔除 {empty_mask.sum()} 行毫无意义的无效去重键的数据。")
        data_df = data_df[~empty_mask]

    logger.info("\n=== 识别重复数据 ===")
    data_df['is_duplicate'] = data_df.duplicated(subset=['_dedup_key'], keep=False)
    logger.info(f"涉及重复的总行数: {data_df['is_duplicate'].sum()}")
    
    duplicates_df = data_df[data_df['is_duplicate']].copy()
    _export_duplicate_details(duplicates_df, output_dir, ref_col)

    return data_df

# =========================================================================
# 阶段二调度器：执行数据融合与组装 (Phase 2)
# =========================================================================
def fuse_and_assemble_dataset(data_df, property_col, drop_columns, ref_col='ref', agg_method='mean'):
    """
    第二阶段：基于第一阶段的标记，执行物理化学约束下的数据融合，并输出最终干净的数据集。
    """
    if data_df is None or data_df.empty:
        logger.error("错误：传入的数据集为空，无法执行融合。")
        return None

    duplicates_df = data_df[data_df['is_duplicate']].copy()
    non_duplicates_df = data_df[~data_df['is_duplicate']].copy()

    processed_duplicates_df = _fuse_duplicate_groups(duplicates_df, property_col, ref_col, agg_method=agg_method)

    # 5. 组装最终结果
    logger.info("\n=== 生成最终数据集 ===")
    final_df = pd.concat([non_duplicates_df, processed_duplicates_df], ignore_index=True)
    
    cols_to_remove = ['is_duplicate', '_dedup_key', '_dedup_number', '_group_size', 'original_excel_index'] + drop_columns
    final_df.drop(columns=[c for c in cols_to_remove if c in final_df.columns], inplace=True, errors='ignore')
    final_df.reset_index(drop=True, inplace=True)
    
    logger.info(f"最终数据集构建完成。总行数: {len(final_df)}")
    return final_df