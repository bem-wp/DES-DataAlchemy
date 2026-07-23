import pandas as pd
import os
import shutil
import json
import logging

from app_configs import SiSelectorConfig

logger = logging.getLogger(__name__)

def get_files_with_data(excel_path):
    # 读取Excel文件
    si_screening_results = pd.read_csv(excel_path)
    # 筛选出“数据状态”列为“有”的行
    yes_files = si_screening_results[si_screening_results['数据状态'] == '有']
    # 将筛选结果中的“文件名”列转换成列表并返回
    file_list = yes_files['文件名'].tolist()
    return file_list

def select_yes_files(cfg: SiSelectorConfig) -> None:
    """
    根据预筛选结果 Excel，从 PDF 源文件夹中挑选“有数据”的文件，
    复制到指定的输出文件夹中。

    Args:
        cfg: SiSelectorConfig 实例，包含以下字段：
            - si_screening_result: 预筛选 Excel 文件路径
            - pdf_input_folder: 存放所有待筛 PDF 的源文件夹
            - pdf_output_folder : 复制目标文件夹
    """
    # 1. 从 Excel 中获取“有数据”的文件名列表
    if not os.path.exists(cfg.si_screening_result):
        logger.error(f"预筛选结果文件不存在: {cfg.si_screening_result}")
        return

    files_to_copy = get_files_with_data(cfg.si_screening_result)
    total = len(files_to_copy)
    logger.info(f"从预筛选结果中获取到 {total} 个“有数据”的文件")

    if total == 0:
        logger.warning("没有需要复制的文件，流程结束")
        return

    # 2. 准备目标文件夹
    os.makedirs(cfg.pdf_output_folder , exist_ok=True)

    # 3. 逐个复制文件
    success_count = 0
    missing_count = 0
    source_dir = cfg.pdf_input_folder
    target_dir = cfg.pdf_output_folder 

    for filename in files_to_copy:
        src = os.path.join(source_dir, filename)
        dst = os.path.join(target_dir, filename)

        if os.path.exists(src):
            shutil.copy2(src, dst)
            success_count += 1
        else:
            logger.warning(f"源文件不存在，跳过: {filename}")
            missing_count += 1

    # 4. 输出统计信息
    logger.info("-" * 40)
    logger.info(f"文件挑选完成: 成功 {success_count} 个，缺失 {missing_count} 个")
    logger.info(f"目标文件夹: {os.path.abspath(target_dir)}")

