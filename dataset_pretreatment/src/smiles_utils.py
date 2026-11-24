from functools import lru_cache

import pandas as pd
from rdkit import Chem, RDLogger

# 批量解析时 RDKit 会为每个失败样本打印日志，这里统一关闭，由调用方汇总审计。
RDLogger.DisableLog("rdApp.*")

_NULL_TEXT = {"", "nan", "none", "null"}


def _is_missing(value) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=None)
def _canonicalize_str(smiles: str):
    try:
        mol = Chem.MolFromSmiles(smiles)
    except Exception:
        return None
    if mol is None:
        return None
    try:
        return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    except Exception:
        return None


def canonicalize_smiles(value):
    """空值返回 None；无法解析返回 None；否则返回 RDKit canonical isomeric SMILES。"""
    if _is_missing(value):
        return None
    text = str(value).strip()
    if text.lower() in _NULL_TEXT:
        return None
    return _canonicalize_str(text)


def canonicalize_smiles_or_original(value):
    """空值返回 None；无法解析时保留原始字符串；否则返回 canonical SMILES。"""
    if _is_missing(value):
        return None
    text = str(value).strip()
    if text.lower() in _NULL_TEXT:
        return None
    canonical = _canonicalize_str(text)
    return text if canonical is None else canonical
