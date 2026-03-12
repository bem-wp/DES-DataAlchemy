import pandas as pd
import numpy as np
import os
import re
from openpyxl import load_workbook
from openpyxl.styles import Alignment

def data_quality_summary(df, output_path):
    """
    生成简单的数据质量报告
    """
    summary = {
        '总行数': len(df),
        '列清单': str(df.columns.tolist()),
        '缺失值统计': df.isnull().sum().to_dict()
    }
    
    with open(output_path.replace('.xlsx', '.txt'), 'w', encoding='utf-8') as f:
        for k, v in summary.items():
            f.write(f"{k}: {v}\n")
    print(f"质量报告已生成: {output_path.replace('.xlsx', '.txt')}")

def process_dataset_deduplication(original_df, essential_cols, str_cols, num_cols, property_col, drop_columns, output_dir, ref_col='ref', temp_col=None):
    """
    执行具备“置换不变性”的混合物数据集去重 (即 A+B == B+A)。
    
    【核心业务假设与物理约束说明】:
    1. 组分缺失等同于含量为0：物理化学中，未报告的组分即代表体系中不存在该物质。
    2. 摩尔分数求和约束 (Σx_i = 1)：锁定组分比例约束，不对 x 取均值。
    3. 数值截断去噪：消除实验精度噪音。
    """
    # 严格的参数校验
    assert len(str_cols) == len(num_cols), "错误：组分名称列(str_cols)和数值列(num_cols)的长度必须一致！"
    
    os.makedirs(output_dir, exist_ok=True)
    data_df = original_df.copy()
    
    # 记录原始索引，方便溯源
    data_df['original_index'] = data_df.index
    
    print("=== [Utils] 1. 缺失值处理 ===")
    missing_cols = [c for c in essential_cols if c not in data_df.columns]
    if missing_cols:
        print(f"错误: 输入数据缺少关键列: {missing_cols}")
        return None

    null_mask = data_df[essential_cols].isnull().any(axis=1)
    if null_mask.any():
        print(f"检测到 {null_mask.sum()} 行包含缺失值，正在删除。")
        data_df = data_df.dropna(subset=essential_cols)
    else:
        print("未检测到关键列的缺失值。")
    print(f"剩余行数: {len(data_df)}")

    # =========================================================================
    # 2. 生成规范化去重键
    # =========================================================================
    if temp_col:
        print(f"\n=== [Utils] 2. 生成规范化 Key (解决 A+B vs B+A 问题，包含温度: {temp_col}) ===")
        cols_to_numeric = num_cols + [temp_col]
    else:
        print("\n=== [Utils] 2. 生成规范化 Key (解决 A+B vs B+A 问题，不包含温度) ===")
        cols_to_numeric = num_cols

    temp_nums = data_df[cols_to_numeric].apply(pd.to_numeric, errors='coerce').fillna(0)
    
    def generate_key(row):
        pairs = []
        for c_col, x_col in zip(str_cols, num_cols):
            c_val = row[c_col]
            x_val = temp_nums.at[row.name, x_col]
            
            if pd.isna(c_val) or str(c_val).strip() == "":
                continue
            
            c_clean = str(c_val).strip().lower()
            x_clean = "{:.4f}".format(x_val)
            pairs.append((c_clean, x_clean))
        
        # 稳定的双重排序 (Tie-breaking)
        pairs.sort(key=lambda x: (x[0], x[1]))
        comp_key = "~~".join([f"{p[0]}::{p[1]}" for p in pairs])
        
        if temp_col:
            t_val = temp_nums.at[row.name, temp_col]
            t_key = "T::{:.2f}".format(t_val)
            return f"{comp_key}~~{t_key}" if comp_key else f"~~{t_key}"
        else:
            return comp_key

    print("正在生成唯一标识键...")
    data_df['_dedup_key'] = data_df.apply(generate_key, axis=1)
    
    # 剔除无效 Key
    if temp_col:
        empty_key_mask = (data_df['_dedup_key'] == "") | (data_df['_dedup_key'].str.startswith("~~T::"))
    else:
        empty_key_mask = (data_df['_dedup_key'] == "")

    if empty_key_mask.any():
        print(f"警告: {empty_key_mask.sum()} 行生成了无效 Key，将被剔除。")
        data_df = data_df[~empty_key_mask]

    # =========================================================================
    # 3. 识别重复值并保存详情
    # =========================================================================
    print("\n=== [Utils] 3. 识别重复数据 ===")
    data_df['is_duplicate'] = data_df.duplicated(subset=['_dedup_key'], keep=False)
    total_duplicates = data_df['is_duplicate'].sum()
    print(f"涉及重复的总行数: {total_duplicates}")
    
    duplicates_df = data_df[data_df['is_duplicate']].copy()
    non_duplicates_df = data_df[~data_df['is_duplicate']].copy()

    if not duplicates_df.empty:
        duplicates_df['_dedup_number'] = duplicates_df.groupby('_dedup_key').ngroup() + 1
        sorted_dups = duplicates_df.sort_values(by=['_dedup_number', ref_col])
        
        # 把 original_index 放在最前面（紧跟编号和 Key），让追溯极其方便
        cols = ['_dedup_number', '_dedup_key', 'original_index'] + \
               [c for c in sorted_dups.columns if c not in ['_dedup_number', '_dedup_key', 'original_index']]
        sorted_dups = sorted_dups[cols]
        
        detail_path = os.path.join(output_dir, '00_duplicated_data_details.xlsx')
        sorted_dups.to_excel(detail_path, index=False)
        
        # 尝试合并单元格优化可读性，允许降级
        try:
            print(f"正在优化 Excel 格式 (合并重复组编号)...")
            wb = load_workbook(detail_path)
            ws = wb.active
            col_idx, start_row, max_row = 1, 2, ws.max_row
            prev_val = ws.cell(row=2, column=col_idx).value
            
            for row in range(3, max_row + 2):
                curr_val = ws.cell(row=row, column=col_idx).value
                if curr_val != prev_val:
                    if row - 1 > start_row:
                        ws.merge_cells(start_row=start_row, start_column=col_idx, end_row=row-1, end_column=col_idx)
                        ws.cell(row=start_row, column=col_idx).alignment = Alignment(horizontal='center', vertical='center')
                    start_row, prev_val = row, curr_val
            wb.save(detail_path)
            print(f"重复数据详情已保存至: {detail_path} (已合并单元格)")
        except Exception as e:
            print(f"合并单元格时出错: {e}")
            print(f"重复数据详情已保存至: {detail_path} (未合并单元格)")
    else:
        print("未发现重复数据。")

    # =========================================================================
    # 4. 合并重复数据
    # =========================================================================
    print("\n=== [Utils] 4. 合并重复数据 ===")
    processed_rows = []
    if not duplicates_df.empty:
        grouped = duplicates_df.groupby('_dedup_key')
        
        for key, group in grouped:
            # 合并 Ref (精准去除结尾的 .0)
            refs = group[ref_col].dropna().astype(str).tolist()
            all_refs = []
            for r in refs:
                r_str = re.sub(r'\.0$', '', r.strip())
                all_refs.extend([x.strip() for x in r_str.split(';')])
            combined_ref = "; ".join(sorted(list(set(all_refs))))
            
            # 强制转化 property_col 为数值型，过滤异常字符串
            prop_values = pd.to_numeric(group[property_col], errors='coerce')
            mean_val = prop_values.mean()
            
            # 取第一行作为模板以锁定组分比例约束
            new_row = group.iloc[0].copy()
            new_row[property_col] = mean_val
            new_row[ref_col] = combined_ref
            
            processed_rows.append(new_row)
            
        processed_duplicates_df = pd.DataFrame(processed_rows)
        print(f"已将 {len(duplicates_df)} 行重复数据压缩为 {len(processed_duplicates_df)} 条唯一记录。")
    else:
        processed_duplicates_df = pd.DataFrame()

    # =========================================================================
    # 5. 生成最终数据集
    # =========================================================================
    print("\n=== [Utils] 5. 生成最终数据集 ===")
    final_df = pd.concat([non_duplicates_df, processed_duplicates_df], ignore_index=True)
    
    # 清理掉辅助列
    cols_to_remove = ['is_duplicate', '_dedup_key', '_dedup_number', 'original_index'] + \
                     [c for c in drop_columns if c in final_df.columns]
    
    final_df.drop(columns=cols_to_remove, inplace=True, errors='ignore')
    final_df.reset_index(drop=True, inplace=True)
    
    print(f"最终数据集构建完成。总行数: {len(final_df)}")
    return final_df