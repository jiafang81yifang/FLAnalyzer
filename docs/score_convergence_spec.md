# 量化足球分析与全链路比分收敛工程规范（v1.0）

用户原文，逐字保留，作为硬性契约。下文第 4 节是工程落地验收口径（协调者补充，非用户原文）。

## 1. 核心设计铁律

1. **单向因果与无对冲原则**：分析链路严格按 `胜平负(1X2) -> 大小球(O/U) -> 31项完备树过滤 -> 市热/市短与盘口联动 -> 六盘口互洽 -> 锁定2个比分` 推进，禁止双边对冲推荐或模棱两可的模糊结论。
2. **数据自洽与零兜底原则**：上游盘口、赔率、水位或预期进球（xG）字段缺失或异常时，严禁静默填充默认值或伪造数据，必须标记缺失状态并中止对应无效信号输出。
3. **时间与结算边界统一**：常规大小球与比分统计严格限定在 90 分钟常规时间加裁判指定伤停补时，明确区分全场总进球与注单确认后净进球；加时赛与点球大战默认不计入常规盘口。

## 2. 六步执行链路规范

- **Step 1（1X2 定向）**：结合模型概率与去水市场隐含概率，输出唯一主导方向（`H` / `D` / `A`）。
- **Step 2（O/U 路径）**：依附 Step 1 方向与盘口基准线，结合预期进球修正与大小球隐含倾向，输出单一路径（`OVER` / `UNDER`）。
- **Step 3（31 项完备树）**：在 31 项比分空间中剔除与 Step 1 / Step 2 冲突的赛果，仅保留逻辑自洽的候选子集。
- **Step 4（联动与市热/市短）**：校验让球盘与大小球盘口的顺局/互殴联动关系，叠加市热方向与短线资金偏向进行权重修正。
- **Step 5（六盘口互洽）**：检查欧赔、亚盘、大小球、半场胜平负、半场大小球、节奏与盘口演变六大维度一致性，记录冲突明细与互洽得分。
- **Step 6（双比分锁定）**：在候选子集中按综合权重排序，刚性输出排名前 2 的比分。

## 3. 异常处理与审计要求

- 滚球确认期间发生进球、红牌、点球或 VAR 改判时，记录事件时间戳与状态变更日志。
- 所有回测与实盘运行均需留存输入快照、各阶段中间结果、互洽得分与最终双比分输出，便于复盘归因。

---

## 4. 工程落地验收口径（协调者补充）

以下不是新需求，而是把上面三节变成"可运行、可验证、可复盘"的判定标准。任何一条不满足即视为未落地。

### 4.1 交付形态

- 新增独立运行时模块 `score_convergence_engine.py`（纯 Python，无 streamlit 依赖，可被单文件构建内嵌、可被 pytest 直接 import）。
- 通过 `scripts/build_single_file.py` 注册进 `FLANALYZER_EMBEDDED_RUNTIME_MODULES`；**提交必须包含重新生成的 `app_v84_hermes.py`**（构建器是就地改写该文件的内嵌块，只改列表不重建会导致每次构建都产生差异、pin 漂移）。
- 入口函数一次调用走完六步并返回完整结果字典（含每步中间产物、互洽得分、冲突明细、锁定双比分、审计快照），不得拆成"需要人工串"的多个半成品。

### 4.2 铁律 1（单向因果 / 无对冲）的机读化

- 结果里必须有明确的 `direction ∈ {H, D, A}`、`ou_path ∈ {OVER, UNDER}`、`locked_scores`（长度恰为 2）。
- 必须存在结构性保证：任何一步缺失都不允许"双选"或"留空当结论"。禁止输出同时包含 H 与 A 的推荐集合、禁止 `locked_scores` 长度不为 2、禁止在缺少 Step1/Step2 结论时继续往下跑并把结果标成有效。
- 需要一条显式断言：Step3 候选集中每个比分的赛果与 Step1 方向一致、总进球方向与 Step2 一致；不满足即抛错（内部错误码，不静默裁剪后当成功）。

### 4.3 铁律 2（零兜底）的机读化

- 每条输入（1X2 市场、大小球盘口、让球盘、半场盘、31 项市场值、xG 等）都要有独立的存在性与有效性检查；缺失/异常时写入 `missing` 或 `invalid` 列表并携带字段名与原因，同时**中止依赖它的那一步输出**（该步结果标 `status="aborted"`，下游不得伪造）。
- 严禁出现 `default=2.5`、`or 0.5`、`if not x: x = <常量>` 这类静默填充；赔率 ≤1.0、概率和明显越界、盘口线非 0.25 倍数（按联赛规则）等都要判为异常而不是修正。
- 需要一条负向测试：删掉任一必需输入，结果必须是"该步中止 + 原因明确"，并且**不得**产出 locked_scores。

### 4.4 铁律 3（时间边界）的机读化

- 所有比分/总进球统计口径参数化为 `regular_time_only=True` 默认值，常量集中在模块顶部（90 + 补时）；加时与点球数据即使存在也必须被排除，并记录"已排除"计数，不得混进 31 项或 O/U 判定。

### 4.5 六步各自的验收点

| 步骤 | 必须产出 | 验收方式 |
|---|---|---|
| Step1 | `direction`, 模型概率、去水市场概率、两者差异 | 手算一场，方向与手算一致；去水后三项和 = 1 |
| Step2 | `ou_path`, 盘口线、xG 修正后期望总进球、上下盘隐含倾向 | 手算：xG 修正把结论从 OVER 翻成 UNDER 的样例各一 |
| Step3 | 候选子集（31 项内）、被剔除项及剔除理由 | 候选数 < 31 且被剔除项理由可枚举；候选全与 Step1/2 自洽 |
| Step4 | 顺局/互殴判定、市热方向、市短偏向、权重修正量 | 顺局与互殴各一例，权重修正方向相反且可复算 |
| Step5 | 六维度逐项得分、冲突明细、总互洽得分（0–100） | 六维全齐时得分可手算；缺一维则该维得 0 且列入冲突，不得补分 |
| Step6 | 排序后的 `locked_scores`（恰 2 条）、每条的综合权重与分解 | 权重分解各项之和等于总分；并列时用确定性平局规则（可复现） |

### 4.6 与现有系统的接线

- 31 项必须复用现有能力，不得另建一套比分空间：`build_pool31_option_rows` / `build_theo_mkt_31_pack` / `resolve_cs31_provenance`（出处 recorded / derived 的语义原样保留，市短/市热矩阵不得回退）。
- 市热/市短来自现有 `heat_tg_lines`、`mkt_over_theo_tg_lines`；六盘口互洽要吃到现有欧赔/亚盘/大小球/半场数据，缺哪个维度就如实报缺，不得用别的维度顶替。
- Telegram 报告必须新增一段"六步收敛"，逐字给出：方向、O/U 路径、候选集大小、互洽得分、冲突明细条数、锁定 2 个比分；缺失原因也要出现在报告里（不许出现"未形成"却不给原因）。

### 4.7 审计与复盘

- 每次运行输出一份结构化快照（输入快照 + 六步中间结果 + 互洽明细 + 最终双比分），可通过现有记录通道落盘/入库，键名稳定，能被回放脚本读回。
- 滚球事件（进球/红牌/点球/VAR）要有事件日志结构：时间戳、事件类型、变更前后状态；未接入真实滚球源时，用显式 `source="not_connected"` 标记，不得伪造事件。

### 4.8 测试要求（不可妥协）

- pytest 新增用例覆盖：六步 happy path、每条铁律的负向用例、Step2 翻转、Step4 顺局/互殴、Step5 缺维、Step6 并列。
- **期望值必须独立手算**（在测试里写出手算过程或常量来源），不得用被测函数自己算期望值。
- 每个新增守卫都要能被"单点 revert 打红"：逐条删除守卫后对应测试必须失败，并在报告里列出每条的 revert 结果。

---

## 5. 规则 → 落地函数 / 守卫 映射

代码全部在 `score_convergence_engine.py`；测试在 `tests/test_score_convergence_engine.py`。

| 规范条款 | 落地位置 | 守卫 / 验收方式 |
|---|---|---|
| §1 铁律 1 单向因果（方向只能来自 Step1） | `step1_direction` | `direction_source ∈ {directive, model_market, pairwise, absolute_fallback}`；`direction_postcheck` 不允许任何下游写方向 |
| §1 铁律 1 禁止对冲 / 双选 | `run_score_convergence` 尾部 | 同向断言：`locked_scores` 中不得出现与 `direction` 相反的桶，抛 `hedge_direction_in_locked`；`locked_scores_identical` 禁止 ①==② |
| §1 铁律 1 长度恰为 2 | `step6_lock` + `run_score_convergence` | 候选 < 2 即 `aborted`；`locked_scores_length` 断言 |
| §1 铁律 2 零兜底 | `_finite`、各 `step*` 的 `missing`/`invalid` | 所有输入先判定再使用；`_finite` 只返回 `float` 或 `None`，不返回默认值 |
| §1 铁律 2 赔率 ≤1.0 判异常 | `_de_vig` | 返回 `invalid_reason`，`step1` 置 `aborted`，不修正 |
| §1 铁律 2 盘口线非 0.25 倍数判异常 | `normalize_handicap_line` | `quarters` 非整数即 `valid=False`，不做就近取整 |
| §1 铁律 3 常规时间口径 | `REGULAR_TIME_MINUTES` / `STOPPAGE_MINUTES_MAX` / `REGULAR_TIME_ONLY`、`live_event_log` | 加时/点球进 `excluded` 并计数；`regular_time_only` 可显式关闭 |
| §2 Step1 唯一方向 | `step1_direction` | 四档顺序，全部无解即 `aborted`，绝不默认平 |
| §2 Step2 单一路径 | `step2_ou_path` | 只输出 `OVER`/`UNDER`；模型与市场不同向时记 `PATH_CONFLICT_MODEL_WINS` |
| §2 Step3 31 项完备树 | `build_complete_tree_31` | 恒 31 项；`aggregate_in_tree == 3`；聚合桶不被结构性剔除 |
| §2 Step4 顺局/互殴 + 市热/市短 | `step4_linkage` | `linkage ∈ {顺局, 互殴}`，乘数 1.10 / 0.90；市热/市短只消费 `heat_tg_lines` / `mkt_over_theo_tg_lines` |
| §2 Step5 六维互洽 | `step5_consistency` | `DIMENSION_WEIGHTS` 合计 100；缺维 0 分并进 `conflicts`，不补分 |
| §2 Step6 双比分锁定 | `step6_lock` | 权重分解 `WEIGHT_COMPONENTS` 之和 == `total`；并列按 `total desc -> market desc -> score asc` |
| §3 滚球事件日志 | `live_event_log` | 未接真实源时 `source="not_connected"`；事件含 ts/type/before/after |
| §4.1 单入口 | `run_score_convergence` | 一次调用返回全部六步中间产物 |
| §4.1 构建注册 | `scripts/build_single_file.py` 的 `MODULES` | 内嵌块由脚本生成，提交含重新生成的 `app_v84_hermes.py` |
| §4.2 Step3 自洽断言 | `step3_candidate_tree` 尾部 | 逐项复查方向/总进球，不符抛 `ConvergenceContractError` |
| §4.4 口径参数化 | 模块顶部常量 | `regular_time_only` 参数 |
| §4.6 复用 31 项能力 | `score_matrix_from_pack`、`build_pool31_option_rows`（app 侧） | 不另建比分空间；`market_source` 逐行透传 |
| §4.6 出处语义保留 | `resolve_pool31_provenance` | 委托 `DeepSeekAnalyzer.resolve_cs31_provenance`，`source ∈ {recorded, derived, None}` |
| §4.6 Telegram 六步收敛段 | `format_convergence_tg_lines`、`build_six_step_convergence_section`（app） | 六项逐字输出；aborted 必带原因，裸「未形成」由测试拦截 |
| §4.7 结构化审计快照 | `AuditLog`、`audit_snapshot` | 键名固定；`audit_logs` 每条含 `ts/step/code/detail` |
| §4.8 手算期望值 | `tests/test_score_convergence_engine.py` | 每个断言上方写明手算过程与来源常量 |
| §4.8 单点 revert 可打红 | 每个守卫一条测试 | 见 PR 报告里的 revert 矩阵 |
