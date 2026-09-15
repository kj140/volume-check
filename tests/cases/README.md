# tests/cases — 手計算ケース

手で計算して検算した具体例。**期待値が先、実装が後**（CLAUDE.md 3）。

```
tests/cases/<名前>/
  input.json     solve() への入力（samples/*.json と同じ形、単位 m）
  calc.md        計算過程（どの条文のどの式で、どう数字が出るか）と許容差
  expected.json  期待値（表示単位 m・m²）と許容差
```

`tests/test_cases.py` が expected.json を現在の実装と突き合わせる。

## 一覧

| ケース | 内容 | 出どころ |
|---|---|---|
| `hand_calc_road12` | 20m×30m 矩形・商業・道路12m。建蔽率の絞り込み量 t、道路斜線・隣地斜線の後退、外壁後退と斜線は足さず max、9階で容積率打ち切り | `tests/test_solver.py` の `test_hand_calc_bcr_inset` / `test_hand_calc_road12` / `test_hand_calc_setbacks_take_the_larger_not_the_sum` |
| `hand_calc_road6` | 同じ敷地で道路6m。前面道路幅員による容積率の低減（600%→360%）、5階で打ち切り | `tests/test_solver.py` の `test_hand_calc_road6` |

M00 で `tests/test_solver.py` の手計算ケースをこの形に写した。元のテスト関数はそのまま残している
（同じ期待値を2か所で持つが、こちらは「計算過程を文書として読める」ことが目的）。
