"""正規化とハッシュ（docs/architecture.md 4.5・5章「キャッシュと再計算」）。

正規化 JSON：キーを並べ替え、float を小数 3 桁に丸め、空白を入れない。
保存する値そのものは丸めない（丸めるのはハッシュを取るときだけ）。

案の input_hash は、敷地事実と計画条件の**値**から作る。出所（source / origin /
retrieved_at / note）は含めない。同じ値なら誰がいつ入れても同じ算定になるため
（docs/decisions/0003）。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from model.types import PlanConditions, SiteFacts

FLOAT_DIGITS = 3


def _normalize(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"ハッシュに使えない値: {value!r}")
        rounded = round(value, FLOAT_DIGITS)
        return 0.0 if rounded == 0 else rounded        # -0.0 を 0.0 に揃える
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if hasattr(value, "model_dump"):
        return _normalize(value.model_dump(mode="json"))
    raise TypeError(f"ハッシュに使えない型: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """キーを並べ替え、float を小数 3 桁に丸めた JSON 文字列。"""
    return json.dumps(_normalize(value), ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, allow_nan=False)


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def input_hash(site_facts: SiteFacts, plan_conditions: PlanConditions) -> str:
    """案の input_hash。敷地事実と計画条件の値だけから作る。"""
    return sha256_of({"site_facts": site_facts.values(),
                      "plan_conditions": plan_conditions.values()})
