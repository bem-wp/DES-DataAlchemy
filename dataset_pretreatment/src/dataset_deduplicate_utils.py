import pandas as pd
import numpy as np
import os
import re
from openpyxl import load_workbook
from openpyxl.styles import Alignment
import logging

from smiles_utils import canonicalize_smiles, canonicalize_smiles_or_original

logger = logging.getLogger(__name__)

_NULL_TEXT = {"", "nan", "none", "null"}
# 归一化摩尔分数后用于去重键的小数位数（只影响键，不改变输出表的 x* 列）。
COMPOSITION_DECIMALS = 4

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


class RemovalTracker:
    """统一收集各清洗步骤删除的行：逐行附删除原因 → 紧凑打印 → 汇总写入单个 Excel。

    约定：每个删除步骤调用 :meth:`record` 标记原因并立即打印；调度器在末尾调用
    :meth:`to_excel` 将所有被删行（含 ``_removal_stage`` 与 ``_invalid_reason``）
    合并落盘到 ``raw-ds_removed_rows_log.xlsx``。
    """

    AUDIT_FILENAME = 'raw-ds_removed_rows_log.xlsx'

    def __init__(self, output_dir, index_col='original_excel_index'):
        self.output_dir = output_dir
        self.index_col = index_col
        self._frames = []
        self.total = 0

    def record(self, df, mask, stage, reasons):
        """记录 ``df[mask]`` 为删除行，紧凑打印后返回保留下来的 ``df[~mask]``。

        reasons: 与删除行对齐的 Series / {index: reason} dict / 标量字符串。
        """
        if isinstance(mask, pd.Series):
            mask = mask.reindex(df.index, fill_value=False)
        else:
            mask = pd.Series(mask, index=df.index)

        removed = df.loc[mask]
        if removed.empty:
            return df

        removed = removed.copy()
        if isinstance(reasons, pd.Series):
            reason_values = reasons.reindex(removed.index).astype(str).tolist()
        elif isinstance(reasons, dict):
            reason_values = [str(reasons.get(i, stage)) for i in removed.index]
        else:
            reason_values = [str(reasons)] * len(removed)

        removed.insert(0, '_invalid_reason', reason_values)
        removed.insert(0, '_removal_stage', stage)
        self._frames.append(removed)
        self.total += len(removed)

        print(f"【删除审计】{stage}: 删除 {len(removed)} 行")
        row_ids = (removed[self.index_col].tolist()
                   if self.index_col in removed.columns else removed.index.tolist())
        for rid, reason in zip(row_ids, removed['_invalid_reason'].tolist()):
            print(f"  - 行 {rid}: {reason}")

        return df.loc[~mask].copy()

    def to_excel(self):
        """把所有删除行合并写入单个审计 Excel；无删除记录时写空表头。"""
        if not self.output_dir:
            return None
        path = os.path.join(self.output_dir, self.AUDIT_FILENAME)
        if self._frames:
            all_removed = pd.concat(self._frames, ignore_index=True)
        else:
            all_removed = pd.DataFrame(
                columns=[self.index_col, '_removal_stage', '_invalid_reason'])
        front = [c for c in (self.index_col, '_removal_stage', '_invalid_reason') if c in all_removed.columns]
        rest = [c for c in all_removed.columns if c not in front]
        all_removed = all_removed[front + rest]
        all_removed.to_excel(path, index=False)
        logger.info(f"删除审计汇总: 共 {self.total} 行被删除记录，已保存至 {path}")
        return path


def _clean_missing_data(df, essential_cols, tracker):
    """关键字段缺失检查与清理：逐行记录缺失的关键列后删除。"""
    logger.info("\n=== step 1: 关键字段缺失处理 ===")
    missing_cols = [c for c in essential_cols if c not in df.columns]
    if missing_cols:
        logger.error(f"错误: 数据集缺少关键列，无法构成DES体系: {missing_cols}")
        return None

    miss = df[essential_cols].isnull()
    null_mask = miss.any(axis=1)
    if null_mask.any():
        miss_rows = miss.loc[null_mask]
        reasons = miss_rows.apply(
            lambda r: '关键字段缺失: ' + ','.join(miss_rows.columns[r.values]), axis=1)
        df = tracker.record(df, null_mask, '关键字段缺失', reasons)
    else:
        logger.info("未检测到在关键字段有缺失的记录。")
    logger.info(f"剩余记录数: {len(df)}")
    return df

def _filter_and_log_invalid_numerics(df, num_cols, state_var_cols, tracker):
    """数值/状态变量非法（非空却转不成数字）的行，记录非法列后删除。"""
    logger.info("\n=== step 2: 异常数值审查与拦截 ===")

    if state_var_cols is None:
        state_vars = []
    elif isinstance(state_var_cols, str):
        state_vars = [state_var_cols]
    else:
        state_vars = list(state_var_cols)

    cols_to_check = num_cols + state_vars

    per_col_bad = {}
    invalid_mask = pd.Series(False, index=df.index)
    for col in cols_to_check:
        if col not in df.columns: continue

        # 原本非空，但强转数字后变成 NaN 的，就是非法字符
        is_not_empty = df[col].notna() & (df[col].astype(str).str.strip() != "") # 找出真正写了东西的单元格
        is_invalid_num = pd.to_numeric(df[col], errors='coerce').isna() # 找出转不成数字的单元格
        bad = is_not_empty & is_invalid_num
        per_col_bad[col] = bad
        invalid_mask |= bad # 把当前列的脏数据标记累积到总掩码中

    if invalid_mask.any():
        logger.warning(f"⚠️拦截到 {int(invalid_mask.sum())} 行包含非法数值的数据！")
        bad_df = pd.DataFrame(per_col_bad).loc[invalid_mask]
        reasons = bad_df.apply(
            lambda r: '非法数值(无法转数字): ' + ','.join(bad_df.columns[r.values]), axis=1)
        df = tracker.record(df, invalid_mask, '非法数值', reasons)
        logger.info(f" └─ 剔除后剩余有效行数: {len(df)}")
    else:
        logger.info(" └─ 未发现非法数值")

    return df


def _validate_compositions(df, num_cols, tracker):
    """校验摩尔配比：必须为有限非负数，且行和大于 0；逐行记录原因后删除非法行。"""
    logger.info("\n=== step 2.2: 配比合法性校验 ===")

    missing_cols = [c for c in num_cols if c not in df.columns]
    if missing_cols:
        logger.error(f"错误: 数据集缺少配比列: {missing_cols}")
        return None

    x = df[num_cols].apply(pd.to_numeric, errors='coerce').astype(float)
    finite = np.isfinite(x)
    nonneg = x >= 0
    row_sum = x.sum(axis=1)

    invalid_mask = ~(finite.all(axis=1) & nonneg.all(axis=1) & (row_sum > 0))

    if invalid_mask.any():
        logger.warning(
            f"剔除 {int(invalid_mask.sum())} 行非法配比数据"
            f"（非有限/负值/非数值/配比和<=0）。")
        sub = x.loc[invalid_mask]
        sub_finite = np.isfinite(sub)
        sub_sum = sub.sum(axis=1)
        reasons = []
        for idx in sub.index:
            parts = []
            bad_cols = [c for c in num_cols if not sub_finite.at[idx, c]]
            if bad_cols:
                parts.append('配比非数值/非有限:' + ','.join(bad_cols))
            neg_cols = [c for c in num_cols if sub.at[idx, c] < 0]
            if neg_cols:
                parts.append('配比为负:' + ','.join(neg_cols))
            if sub_finite.loc[idx].all() and not (sub_sum.at[idx] > 0):
                parts.append('配比和<=0')
            reasons.append('; '.join(parts) if parts else '配比非法')
        reasons = pd.Series(reasons, index=sub.index)
        df = tracker.record(df, invalid_mask, '配比非法', reasons)
    else:
        logger.info("未发现非法配比。")

    return df


def _filter_zero_essential_ratio(df, essential_ratio_cols, tracker):
    """关键配比列（如 x1、x2）任一值为 0 即视为脏数据，整行删除。

    ``essential_ratio_cols`` 由 num_cols 与 essential_cols 的交集推导（通常为 ['x1','x2']）；
    x3 这类可选组分不在其中，配比可为 0。
    """
    logger.info("\n=== step 2.3: 关键配比列零值校验 ===")

    cols = [c for c in essential_ratio_cols if c in df.columns]
    if not cols:
        logger.info("无关键配比列需要零值校验，跳过。")
        return df

    x = df[cols].apply(pd.to_numeric, errors='coerce')
    zero_mask = (x == 0).any(axis=1)

    if zero_mask.any():
        logger.warning(f"检测到 {int(zero_mask.sum())} 行关键配比列（{cols}）值为 0，已直接删除。")
        sub = x.loc[zero_mask]
        reasons = sub.apply(
            lambda r: '; '.join(f'{c}=0' for c in cols if r[c] == 0), axis=1)
        df = tracker.record(df, zero_mask, '关键配比=0', reasons)
        logger.info(f" └─ 剔除后剩余有效行数: {len(df)}")
    else:
        logger.info(f"未发现关键配比列 {cols} 为 0 的记录。")
    return df


def _clean_missing_component_ids(df, str_cols, num_cols, tracker):
    """剔除组分标识不合规的行，逐行记录原因。

    逐组分判断：
    1. 配比 > 0：名称和对应 ``c*_ismiles`` 必须同时存在；
    2. 配比 = 0：只要名称或 ``c*_ismiles`` 任意一个存在，即删除整行；
    3. 配比 = 0 且名称、``c*_ismiles`` 均不存在：保留行，作为空占位。
    """
    logger.info("\n=== step 2.5: 组分标识缺失处理 ===")

    checks = []
    for s_col, n_col in zip(str_cols, num_cols):
        if not s_col.endswith("_ismiles"):
            continue
        name_col = s_col[: -len("_ismiles")]
        if name_col not in df.columns:
            logger.warning(f"跳过组分标识校验：未找到名称列 '{name_col}'（对应 '{s_col}'）")
            continue

        name_present = df[name_col].notna() & (df[name_col].astype(str).str.strip() != "")
        id_missing = df[s_col].fillna("").astype(str).str.strip().str.lower().isin(_NULL_TEXT)
        id_present = ~id_missing
        x = pd.to_numeric(df[n_col], errors='coerce').fillna(0.0)
        active = x > 0
        zero = x == 0

        # 配比>0 但名称或 SMILES 缺失
        active_bad = active & (~name_present | id_missing)
        # 配比=0 但仍有名称或 SMILES（含只有 SMILES、只有名称、两者都有）
        zero_with_identity = zero & (name_present | id_present)
        checks.append((name_col, n_col, active_bad, zero_with_identity))

    if not checks:
        return df

    active_bad_mask = pd.concat([a for _, _, a, _ in checks], axis=1).any(axis=1)
    zero_identity_mask = pd.concat([b for _, _, _, b in checks], axis=1).any(axis=1)
    invalid_mask = active_bad_mask | zero_identity_mask
    if not invalid_mask.any():
        logger.info("未发现组分标识缺失的记录。")
        return df

    reasons = []
    for idx in df.index[invalid_mask]:
        parts = []
        for name_col, n_col, active_bad, zero_with_identity in checks:
            if active_bad.at[idx]:
                name_value = df.at[idx, name_col]
                if pd.isna(name_value) or str(name_value).strip() == "":
                    parts.append(f"{n_col}:配比>0但名称缺失")
                else:
                    parts.append(f"{n_col}:配比>0但SMILES缺失")
            if zero_with_identity.at[idx]:
                parts.append(f"{n_col}:配比=0但仍填名称/SMILES")
        reasons.append("; ".join(parts) if parts else "组分标识不合规")
    reasons = pd.Series(reasons, index=df.index[invalid_mask])

    logger.warning(
        f"配比>0但组分标识缺失: {int(active_bad_mask.sum())} 行；"
        f"配比=0但仍填名称/SMILES: {int(zero_identity_mask.sum())} 行。")
    df = tracker.record(df, invalid_mask, '组分标识不合规', reasons)
    return df


def _generate_invariant_keys(df, str_cols, num_cols, state_var_cols=None, output_dir=None):
    if state_var_cols is None:
        state_vars = []
    elif isinstance(state_var_cols, str):
        state_vars = [state_var_cols]
    else:
        state_vars = list(state_var_cols)

    if state_vars:
        logger.info(f"\n=== step3: 构建数值去重键 (含状态变量: {state_vars}) ===")
    else:
        logger.info("\n=== step3: 构建数值去重键 (仅摩尔分数) ===")

    # 1. 组分身份：RDKit canonical SMILES（无法解析时保留原串并写入审计文件）
    identity_cols = {}
    failure_records = []
    for s_col in str_cols:
        if s_col not in df.columns:
            raise KeyError(f"缺少组分标识列: {s_col}")

        identity_cols[s_col] = df[s_col].map(canonicalize_smiles_or_original)

        for idx, value in df[s_col].items():
            if pd.isna(value):
                continue
            text = str(value).strip()
            if text.lower() in _NULL_TEXT:
                continue
            if canonicalize_smiles(text) is None:
                failure_records.append({
                    "original_excel_index": (
                        df.at[idx, "original_excel_index"]
                        if "original_excel_index" in df.columns
                        else idx + 2
                    ),
                    "column": s_col,
                    "smiles": text,
                })

    if failure_records:
        if output_dir:
            failure_path = os.path.join(output_dir, "raw-ds_invalid_smiles_log.xlsx")
            pd.DataFrame(failure_records).to_excel(failure_path, index=False)
            logger.warning(
                f"检测到 {len(failure_records)} 个无法解析的 SMILES，已保留原串用于去重，"
                f"审计文件: {failure_path}"
            )
        else:
            logger.warning(f"检测到 {len(failure_records)} 个无法解析的 SMILES，已保留原串用于去重。")

    # 2. 组分比例：只对“有合法组分标识且配比 > 0”的组分求和并归一化。
    x_raw = df[num_cols].apply(pd.to_numeric, errors='coerce').astype(float)
    finite = np.isfinite(x_raw)
    identity_valid = {
        s_col: identity_cols[s_col].notna()
        & (identity_cols[s_col].fillna("").astype(str).str.strip() != "")
        for s_col in str_cols
    }
    valid_mask = pd.DataFrame(
        {
            s_col: identity_valid[s_col]
            & (x_raw[n_col] > 0)
            & finite[n_col]
            for s_col, n_col in zip(str_cols, num_cols)
        },
        index=df.index,
    )

    x_for_key = x_raw.copy()
    for s_col, n_col in zip(str_cols, num_cols):
        x_for_key[n_col] = x_for_key[n_col].where(valid_mask[s_col], 0.0)

    sums = x_for_key.sum(axis=1)
    no_valid = (~valid_mask.any(axis=1)) | (sums <= 0) | (~np.isfinite(sums))
    x_norm = x_for_key.div(sums.where(sums > 0, 1.0), axis=0)

    # 3. 向量化拼接 ci 和归一化后的 xi（只用于键，不改输出表）
    pair_df = pd.DataFrame(index=df.index, dtype=object)
    for s_col, n_col in zip(str_cols, num_cols):
        s_series = identity_cols[s_col].fillna("").astype(str)
        mask = valid_mask[s_col]
        n_series = x_norm[n_col].map(f"{{:.{COMPOSITION_DECIMALS}f}}".format)
        pair_df[s_col] = np.where(mask, s_series + "::" + n_series, None)

    # 4. 逐行按字符串排序并连接（置换不变）
    pair_matrix = pair_df.values
    comp_keys = []
    for row in pair_matrix:
        valid_pairs = [x for x in row if pd.notna(x)]
        valid_pairs.sort()
        comp_keys.append("~~".join(valid_pairs))

    comp_keys_series = pd.Series(comp_keys, index=df.index)
    comp_keys_series.loc[no_valid] = ""

    # 4.1 空键兜底提示（删除与留痕在调度器统一处理；正常情况下前置校验已提前剔除）
    if no_valid.any():
        logger.warning(
            f"检测到 {int(no_valid.sum())} 行无有效组分（空去重键），将在调度器统一删除审计。")

    # 5. 动态挂载状态变量（保持原始数值与 2 位小数）
    if state_vars:
        state_nums = df[state_vars].apply(pd.to_numeric, errors='coerce').fillna(0)
        state_series_list = []
        for col in state_vars:
            state_series_list.append(col + "::" + state_nums[col].map('{:.2f}'.format))

        state_suffix_series = state_series_list[0]
        for s in state_series_list[1:]:
            state_suffix_series = state_suffix_series + "~~" + s

        final_keys = np.where(
            comp_keys_series != "",
            comp_keys_series + "~~" + state_suffix_series,
            "~~" + state_suffix_series,
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

    tracker = RemovalTracker(output_dir)

    data_df = original_df.copy()
    data_df['original_excel_index'] = data_df.index + 2

    # 1. 清理关键缺失数据
    data_df = _clean_missing_data(data_df, essential_cols, tracker)
    if data_df is None: tracker.to_excel(); return None

    # 2 拦截并记录含有非法数值的脏数据
    data_df = _filter_and_log_invalid_numerics(data_df, num_cols, state_var_cols, tracker)
    if data_df is None or data_df.empty: tracker.to_excel(); return None

    # 2.2 校验摩尔配比：有限、非负、行和 > 0；非法行直接删除
    data_df = _validate_compositions(data_df, num_cols, tracker)
    if data_df is None or data_df.empty: tracker.to_excel(); return None

    # 2.3 关键配比列（x1、x2 等必需组分）任一为 0 即脏，整行删除
    essential_ratio_cols = [c for c in num_cols if c in essential_cols]
    data_df = _filter_zero_essential_ratio(data_df, essential_ratio_cols, tracker)
    if data_df is None or data_df.empty: tracker.to_excel(); return None

    # 2.5 剔除组分标识不完整的行，避免三元体系退化成二元体系
    data_df = _clean_missing_component_ids(data_df, str_cols, num_cols, tracker)
    if data_df is None or data_df.empty: tracker.to_excel(); return None

    # 3. 生成entry指纹（去重键）
    data_df['_dedup_key'] = _generate_invariant_keys(
        data_df, str_cols, num_cols, state_var_cols, output_dir=output_dir
    )

    # 4. 过滤毫无意义的无效指纹（并入统一删除审计）
    empty_mask = data_df['_dedup_key'].str.startswith("~~") | (data_df['_dedup_key'] == "")
    if empty_mask.any():
        logger.warning(f"警告: 剔除 {int(empty_mask.sum())} 行毫无意义的无效去重键的数据。")
        data_df = tracker.record(
            data_df, empty_mask, '去重键无效', '无有效组分（身份缺失或配比<=0）')

    # 所有清洗步骤完成后，统一落盘删除审计（先逐行 print，再写一个 Excel）
    tracker.to_excel()

    if data_df.empty:
        logger.error("清洗后无有效数据，未生成去重标记。")
        return None

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