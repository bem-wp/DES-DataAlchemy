import json
from pathlib import Path

from app_configs import JsonFilterConfig

# 提取结果的条目键名（新结构固定为 des_entries）
_ENTRIES_KEY = 'des_entries'

# 「无值」的字面量占位符：提取器常把缺失写成字符串 'null'/'none'，与空串一并视为无值 / 无单位
_NULL_TEXT = {'', 'null', 'none'}

# 无量纲物性（solvatochromic 参数 alpha/beta/pi_star）：以纯标量存储、本无单位，
# 且 0 是合法测量值，故豁免「须带单位」与「值不得为 0」两项校验；
# condition_vars（temperature/pressure 等）与有量纲物性（viscosity/density 等）一律要求单位且拒绝 0 值。
_DIMENSIONLESS_PROPERTIES = {'alpha', 'beta', 'pi_star'}


def is_valid_value(value) -> bool:
    """字段是否为「有效值」：非 None、非空列表、非全 null 列表。"""
    if value is None:
        return False
    if isinstance(value, list):
        if not value:  # 空列表
            return False
        if all(v is None for v in value):  # 全是 null
            return False
    return True


def _has_unit(value) -> bool:
    """判断值是否携带非空单位（'null'/'none'/空串视为无单位）：
      - 列表 [值, 单位]    → 取 index 1
      - 对象 {value, unit} → 取 unit
      - 标量 298.15 / "750" → 无单位信息，返回 False
    """
    if isinstance(value, list):
        unit = value[1] if len(value) > 1 else None
    elif isinstance(value, dict):
        unit = value.get('unit')
    else:
        return False
    return unit is not None and str(unit).strip().lower() not in _NULL_TEXT


def _extract_value(raw):
    """从标量 / [值, 单位] / {value, unit} 三种形态取出数值部分（无则返回 None）。"""
    if isinstance(raw, list):
        return raw[0] if raw else None
    if isinstance(raw, dict):
        return raw.get('value')
    return raw


def _is_zero(raw) -> bool:
    """数值是否为 0（提取器常以 0 占位表示缺失/无效）；字符串数值一并识别，非数值返回 False。"""
    value = _extract_value(raw)
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        try:
            return float(value.strip()) == 0.0
        except (ValueError, TypeError):
            return False
    return False


def _value_ok(value, require_unit: bool, reject_zero: bool) -> bool:
    """字段有效性判定：
      - 须为有效值（is_valid_value）
      - require_unit 为真时须携带非空单位
      - reject_zero 为真时数值不得为 0
    """
    if not is_valid_value(value):
        return False
    if require_unit and not _has_unit(value):
        return False
    if reject_zero and _is_zero(value):
        return False
    return True


def filter_des_entries(data, target_property, condition_vars=None):
    """通用过滤：逐条校验 des_entries，仅保留合格条目，并同步过滤 extraction_metadata。

    - 必检字段 components：定义 DES 组成的基础字段，须为非空列表才保留。
    - 条件字段 condition_vars：若非空，则每个变量须「有效值 + 非空单位 + 数值非 0」才保留，
      如 temperature 须带 K/°C 等单位；无单位、单位为空或数值为 0 即丢弃。
    - 目标物性 target_property：须有效值；除无量纲物性 alpha/beta/pi_star 外，
      还须「带非空单位 + 数值非 0」（如 viscosity 须带 cP/mPa·s/Pa·s 等），否则丢弃。
      无量纲物性以纯标量存储、本无单位且 0 为合法值，故豁免单位与 0 值校验
      （见 _DIMENSIONLESS_PROPERTIES）。
    """
    if condition_vars is None:
        condition_vars = []
    if not isinstance(data, dict):
        return data

    extraction = data.get('extraction', {})
    if _ENTRIES_KEY not in extraction:  # 只处理新结构的 des_entries；无该键则原样返回
        return data

    raw_data_entries = extraction[_ENTRIES_KEY]
    has_metadata = 'extraction_metadata' in data and _ENTRIES_KEY in data['extraction_metadata']
    raw_meta_entries = data['extraction_metadata'][_ENTRIES_KEY] if has_metadata else []

    clean_data_entries = []
    clean_meta_entries = []

    for i, entry in enumerate(raw_data_entries):
        # --- 规则 1: components 必检（基础组分定义，须为非空列表）---
        components = entry.get('components')
        should_keep = isinstance(components, list) and bool(components)

        # --- 规则 2: 条件变量须「有效值 + 非空单位 + 数值非 0」，任一不满足即丢弃 ---
        if should_keep:
            for var in condition_vars:
                if not _value_ok(entry.get(var), require_unit=True, reject_zero=True):
                    should_keep = False
                    break

        # --- 规则 3: 目标物性须有效值；非无量纲物性还须「带非空单位 + 数值非 0」 ---
        if should_keep and target_property:
            strict = target_property not in _DIMENSIONLESS_PROPERTIES  # 无量纲物性豁免单位与 0 值校验
            should_keep = _value_ok(entry.get(target_property), require_unit=strict, reject_zero=strict)

        # --- 保留合格条目，并同步其 metadata ---
        if should_keep:
            clean_data_entries.append(entry)
            if has_metadata and i < len(raw_meta_entries):
                clean_meta_entries.append(raw_meta_entries[i])

    data['extraction'][_ENTRIES_KEY] = clean_data_entries
    if has_metadata:
        data['extraction_metadata'][_ENTRIES_KEY] = clean_meta_entries
    return data


def process_filtering(cfg: JsonFilterConfig) -> None:
    """主处理流程：读取配置 → 逐文件过滤 → 写出 *_filtered.json（过滤后无条目则不输出）。"""
    prop_name = cfg.target_property
    cond_vars = cfg.condition_vars
    input_dir = Path(cfg.json_input_folder)
    output_dir = Path(cfg.json_output_folder)

    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        print(f"⚠️ 找不到输入文件夹 '{input_dir}'。请确认 LandingAI 提取脚本已成功运行。")
        return

    print(f"⚙️ 当前过滤目标物性: [{prop_name}]")
    print(f"🌡️ 是否检查状态变量: [{'是' if cond_vars else '否'}]  "
          f"{'(' + ', '.join(cond_vars) + ')' if cond_vars else ''}")
    print(f"📂 开始扫描文件夹: {input_dir} ...\n")

    saved_count = 0
    for doc_path in input_dir.glob("*.pdf_extracted.json"):
        filename = doc_path.name
        new_filename = filename.replace('.pdf_extracted.json', '.pdf_filtered.json')
        output_filepath = output_dir / new_filename

        try:
            with open(doc_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)

            old_len = len(json_data.get('extraction', {}).get(_ENTRIES_KEY, []))
            cleaned_data = filter_des_entries(
                data=json_data,
                target_property=prop_name,
                condition_vars=cond_vars,
            )
            new_len = len(cleaned_data.get('extraction', {}).get(_ENTRIES_KEY, []))

            # 过滤后无有效条目则跳过保存（不产出空文件）
            if new_len == 0:
                print(f" ⚠️ [跳过] {filename} -> 过滤后无有效条目，未保存文件")
                continue

            with open(output_filepath, 'w', encoding='utf-8') as f:
                json.dump(cleaned_data, f, indent=2, ensure_ascii=False)

            print(f" ✅ [处理成功] {filename} -> {new_filename} (条目保留: {old_len} -> {new_len})")
            saved_count += 1

        except Exception as e:
            print(f" ❌ [出错] 处理文件 {filename} 时发生错误: {e}")

    print(f"\n🎉 全部完成！共保存了 {saved_count} 个有效文件。清洗后的数据保存在 {output_dir}")
