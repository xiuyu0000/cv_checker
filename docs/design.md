# CV Checker 技术设计说明

## 架构概览

CV Checker 采用轻量 CLI 编排架构：

- `main.py` 负责命令行参数解析、输入校验和运行模式分发。
- `src/cv_checker/client.py` 封装 NotebookLM SDK 调用。
- `src/cv_checker/checker.py` 实现 legacy 单 prompt 简历审查。
- `src/cv_checker/workflow.py` 实现三阶段面试工作流。
- `src/cv_checker/batch.py` 实现默认 Notebook 初始化和批量简历工作流。
- `src/cv_checker/config.py` 负责 YAML 配置加载和 prompt 完整性校验。
- `config/` 保存 prompt 配置，避免把业务审查逻辑写死在代码中。

这个设计有意保持“薄 Python 编排层 + 外部 NotebookLM 推理层”的边界。Python 代码负责确定流程、参数、文件输出和清理策略；NotebookLM 负责基于上传 sources 做问答、引用和内容生成。

## 为什么这样做

### 使用 NotebookLM 作为外部推理层

项目需要把候选人简历和较长的业务需求资料放在同一个上下文中分析。NotebookLM 已经提供 source 上传、notebook 级资料管理和 chat 问答能力，因此当前实现优先复用它，而不是在本地重新建设 RAG、向量库或文档解析链路。

代价是系统依赖外部服务可用性、NotebookLM 登录状态和 `notebooklm-py` 的行为稳定性。这个依赖被集中封装在 `client.py`，方便未来替换或 mock。

### 使用 YAML 管理 prompt

招聘判断逻辑变化频繁，尤其是“适配证据”“信息缺口”“面试模板”这些问题会随着业务阶段和岗位画像变化。把 prompt 放在 YAML 中有三个好处：

- 非代码修改即可迭代审查标准。
- 可通过 `--config` 对不同岗位或候选人流程使用不同 prompt。
- Python 代码只关心必须字段是否存在，不混入具体业务判断文本。

### 保留 legacy 模式

`checker.py` 中的单 prompt 模式仍然存在，适合快速审查一份 CV 并把结果打印到终端。三阶段工作流是当前更完整的主路径，但 legacy 模式保留了更低成本的入口，也避免破坏已有命令用法。

### 分离长期 sources 和候选人 CV

批量工作流把 `data/sources` 中的业务资料作为长期 sources 保存到默认 Notebook，只把当前候选人 CV 作为临时 source 上传。这样可以复用固定业务上下文，减少重复上传成本，同时仍在每份 CV 完成后删除候选人 source，降低候选人资料长期残留风险。

### 使用 async 边界

NotebookLM SDK 调用是异步的，因此 `client.py`、`checker.py` 和 `workflow.py` 保持 async 接口。`main.py` 只在 CLI 边界用 `asyncio.run()` 启动一次事件循环，避免把同步和异步调用混杂在业务函数内部。

## 数据流

### Legacy 单 prompt 模式

1. CLI 校验 `--notebook-id`、`--cv` 和 CV 文件是否存在。
2. `create_client()` 通过 `NotebookLMClient.from_storage()` 使用本地认证状态创建客户端。
3. `check_cv()` 加载 `config/prompt.yaml` 或自定义配置。
4. 系统校验 `review_prompt` 非空。
5. `add_source()` 将 CV 上传到指定 notebook。
6. `ask_notebook()` 提交 review prompt。
7. CLI 将 NotebookLM 回答打印到标准输出。

### 三阶段面试工作流

1. CLI 校验候选人 CV、候选人姓名、需求资料和 notebook 参数组合。
2. `run_interview_workflow()` 加载 `config/interview_workflow.yaml` 或自定义配置。
3. 系统校验三个工作流 prompt 都存在：`fit_analysis`、`information_gaps`、`interview_template`。
4. 创建输出目录。
5. 如果没有传入 notebook id，调用 `create_notebook()` 创建新 notebook，并用 `add_source(wait=True)` 上传需求资料。
6. 用 `add_source(wait=True)` 上传候选人 CV。
7. 用同一个 `conversation_id` 顺序调用三次 `ask_notebook()`，让后续问题继承前文分析上下文。
8. 将三次回答分别写入：
   - `01_fit_analysis.md`
   - `02_information_gaps.md`
   - `03_interview_template.md`
9. 默认在 `finally` 中调用 `delete_source()` 删除候选人 source。
10. 返回 `InterviewWorkflowResult`，其中包含 notebook id、输出路径、候选人 source id 和删除状态。

### 批量简历面试工作流

1. CLI 校验 `--batch-cv-dir`，并根据 `--notebook-id`、`--default-notebook-config` 和 `--source-dir` 判断是否可以启动。
2. `run_batch_interview_workflow()` 先加载并校验三阶段 prompt 配置，避免处理到中途才发现配置缺失。
3. `ensure_default_notebook()` 按优先级确定默认 Notebook：
   - 使用 CLI 显式传入的 notebook id。
   - 读取 `config/default_notebook.yaml`。
   - 创建 `CV Checker Interview Batch` notebook，并上传 `data/sources` 下的长期 source 文件。
4. Notebook 信息写回 `config/default_notebook.yaml`，只保存 notebook id、source id、source 路径和时间戳。
5. `iter_cv_files()` 扫描 `data/raw/cv` 下一级非隐藏普通文件，按文件名稳定排序。
6. 每份 CV 开始前调用 `cleanup_unrecorded_sources()`，删除 Notebook 中不属于默认长期 sources 的遗留 source。
7. 对每份 CV，`add_source(wait=True)` 上传候选人简历。
8. `extract_candidate_profile()` 调用 NotebookLM 前置 prompt，要求只返回候选人信息 JSON，并通过 `source_ids=[candidate_source_id]` 限定只读取当前 CV。
9. 如果 `candidate_name` 为空，系统允许用文件名第一个 2 到 4 个中文字符 token 作为最后兜底；仍无法得到姓名时，删除候选人 source，记录失败 manifest，并保留本地 CV。
10. 如果提取成功，系统创建唯一候选人输出目录，写入 `00_candidate_profile.json`。
11. `run_interview_prompts()` 复用三阶段 prompt，并通过 `source_ids=default_source_ids + [candidate_source_id]` 限定分析范围。
12. 三阶段回答按阶段立即写入 Markdown，避免后续阶段失败时丢失已完成结果。
13. 系统删除候选人 source。只有删除成功后，才把本地 CV 移动到 `processed` 目录。
14. 每份 CV 结束时追加一条 JSONL manifest，便于排查和续跑。

## 关键接口

### NotebookLM client wrapper

`client.py` 暴露小型函数，而不是让业务代码直接调用 SDK：

- `create_client()`
- `create_notebook(client, title)`
- `add_source(client, notebook_id, file_path, wait=False, wait_timeout=300.0, attempts=3)`
- `delete_source(client, notebook_id, source_id)`
- `ask_notebook(client, notebook_id, prompt, conversation_id=None, source_ids=None, attempts=3)`

这样做的主要原因是测试和替换成本低。`tests/test_workflow.py` 已经通过 patch `workflow.add_source`、`workflow.ask_notebook` 和 `workflow.delete_source` 来验证编排逻辑，而不需要真实浏览器或 NotebookLM 服务。

`add_source()` 会把 `wait_timeout` 传给 NotebookLM SDK，并在上传异常时做有限次数重试。`ask_notebook()` 会把 `source_ids` 显式传给 chat API，并只对 timeout、server disconnected、connection、rate limit、temporarily 等可恢复聊天错误重试。这个策略来自真实批量执行中的故障：不限定 source 会让历史候选人资料参与回答，且长上下文更容易触发超时。

### Workflow result

`InterviewWorkflowResult` 是三阶段工作流的返回对象，包含：

- `notebook_id`
- `output_dir`
- `fit_analysis_path`
- `information_gaps_path`
- `interview_template_path`
- `candidate_source_id`
- `candidate_source_deleted`

CLI 使用这个对象生成最终终端摘要。调用方也可以基于这些路径继续处理输出文档。

`run_interview_prompts()` 是从单份工作流中抽出的公共函数。它假设候选人 CV 已经作为 source 存在于 notebook 中，只负责按配置顺序执行三个 prompt 并写文件。这样批量工作流可以先上传 CV、抽取候选人信息，再复用同一套 prompt 编排，而不会重复上传 CV。

三个 prompt 在同一个 NotebookLM chat session 中连续执行：第一问不传 `conversation_id`，后两问复用前一步返回的 `conversation_id`。批量模式会传入显式 `source_ids`，保证每个候选人的三阶段分析只使用默认长期 sources 和当前候选人 CV。

### Batch result

`batch.py` 定义三个主要结果对象：

- `DefaultNotebook`：当前批处理使用的 notebook id、配置路径、source 目录、source 数量和默认 `source_ids`。
- `BatchItemResult`：单份 CV 的状态、候选人信息、source 删除状态、输出目录、processed 路径、错误信息和 `failure_stage`。
- `BatchWorkflowResult`：批量运行的 notebook id、manifest 路径、成功数、失败数、空目录 skip 计数和单项结果列表。

manifest 使用 JSONL 追加写入。它只记录路径、状态、id、错误和时间戳，不保存原始 CV 内容。

### 配置加载

`load_yaml_config(path)` 是基础 YAML 读取函数。

`load_prompt_config(config_path)` 读取 legacy prompt 配置。

`load_workflow_config(config_path)` 读取三阶段工作流配置，并集中校验必须 prompt。把校验放在配置层，可以让 workflow 逻辑默认拿到完整配置，减少运行到中途才发现 prompt 缺失的风险。

## 错误处理与清理策略

CLI 层处理本地输入错误：

- 文件不存在。
- 必填参数缺失。
- `--notebook-id` 和 `--requirement` 的组合不满足新建 notebook 条件。

业务层处理配置和外部服务错误：

- 缺少配置文件会抛出 `FileNotFoundError`。
- legacy `review_prompt` 为空会抛出 `ValueError`。
- 三阶段 prompt 不完整会抛出 `ValueError`。
- NotebookLM 上传、提问或删除 source 的异常由 SDK 或 wrapper 向上传递。

候选人 source 清理在 `finally` 中执行。这样即使三阶段提问中途失败，只要候选人 source 已上传，系统仍会尝试删除它。删除失败会被记录为 `False`，但不会覆盖更重要的业务异常。

批量模式把“删除 NotebookLM source 成功”作为移动本地 CV 的前置条件。如果删除失败，三阶段报告可能已经写出，但本地 CV 仍留在待处理目录，并在 manifest 中记录失败原因。这个策略优先保证隐私清理明确完成，而不是为了推进队列而隐藏外部服务清理失败。

## 测试设计

当前测试集中在不依赖真实 NotebookLM 的可验证行为：

- `load_workflow_config()` 会拒绝缺少必须 prompt 的配置。
- `run_interview_workflow()` 会按顺序写出三份 Markdown 报告。
- 成功运行后会删除候选人 source。
- NotebookLM 提问失败后仍会尝试清理候选人 source。
- `ensure_default_notebook()` 会创建 notebook、上传长期 sources 并保存默认 Notebook 配置。
- `iter_cv_files()` 会忽略隐藏文件、系统临时文件和目录。
- `run_batch_interview_workflow()` 会在成功时写出候选人 profile、三阶段报告、manifest，并移动本地 CV。
- 候选人姓名抽取失败时不会执行三阶段 prompt，也不会移动本地 CV。
- 候选人姓名抽取为空但文件名有中文姓名前缀时，会使用文件名兜底并标记 `filename_fallback`。
- 批量模式会将 profile 抽取限制在当前候选人 source，将三阶段 prompt 限制在默认 sources 加当前候选人 source。
- 三阶段 prompt 会连续复用 `conversation_id`，并在阶段失败时保留已完成阶段的 Markdown。
- 批量模式会记录具体 `failure_stage`，并避免覆盖 processed 目录中的同名文件。
- 候选人 source 删除失败时不会移动本地 CV。
- `client.py` wrapper 会向 SDK 透传 `wait_timeout`、`source_ids` 和 `conversation_id`，并对可恢复聊天错误重试。

后续适合补充的测试：

- legacy `check_cv()` 对空 `review_prompt` 的校验。
- CLI 参数组合校验。
- 新建 notebook 时 requirement source 的上传顺序。
- `--keep-candidate-source` 对清理行为的影响。
- 批量模式显式 `--notebook-id` 写回默认 Notebook 配置的行为。

## 风险与取舍

- 外部依赖风险：NotebookLM 登录状态、网络状态和 SDK 行为都在本项目控制范围之外。
- 输出质量风险：NotebookLM 输出可能包含错误归因或不完整引用，系统只负责生成辅助材料，不能替代人工判断。
- 隐私风险：候选人 CV 和面试报告可能包含敏感信息，因此默认删除候选人 source，并要求不要提交本地认证数据、CV 文件或生成 artifacts。
- 可扩展性取舍：当前没有抽象成插件化工作流引擎，因为现阶段只有 legacy 审查和三阶段面试流程。保持直接函数编排更容易读、测和修改。
- 错误恢复取舍：批量模式通过 manifest 和“不移动失败 CV”支持简单续跑；NotebookLM 上传和可恢复聊天错误有有限重试，但没有实现断点恢复或部分报告合并。对 CLI 小工具来说，显式保留失败输入比引入复杂状态机更合适。

## 与代理协作文档的关系

仓库已有 `AGENTS.md`，用于告诉 Codex/代码代理如何理解项目结构、命令、测试和安全边界。Claude Code 官方文档也推荐把项目级上下文放在项目记忆文件中。

本设计文档不替代这些代理指令，而是补齐工程实现的“为什么这样做”。未来如果实现发生变化，应同步更新：

- `docs/specs.md`：功能契约和用户可见行为变化。
- `docs/design.md`：架构、数据流、接口和取舍变化。
- `AGENTS.md`：开发命令、测试策略或安全约定变化。
