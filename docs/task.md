# 批量简历面试工作流任务说明

## 任务目标

在 CV Checker 中维护一个批量处理任务：

1. 将 `data/sources` 下的业务资料作为 NotebookLM sources 添加到一个新的 Notebook。
2. 保存该 Notebook 的信息，作为后续运行的默认 Notebook。
3. 用户之后会把候选人简历放入 `data/raw/cv`。
4. 系统需要逐份处理 `data/raw/cv` 中的简历：
   - 上传当前简历到默认 Notebook。
   - 先用大模型从简历中提取候选人必要信息。
   - 使用 `config/interview_workflow.yaml` 的三阶段 prompt 生成回答。
   - 保存该候选人的输出结果。
   - 删除 NotebookLM 中当前简历对应的 source。
   - 成功后把本地简历移动到 processed 目录。
   - 继续处理下一份简历，直到目录中没有待处理简历。

本文档是批量任务的执行契约与当前实现状态，基于 `docs/specs.md` 的产品契约和 `docs/design.md` 的技术设计。当前代码已经实现该批量工作流，后续修改应以本文档作为行为对齐依据。

## 背景与原因

该任务最初用于补齐单份候选人三阶段面试工作流的手动使用问题：

- 需要每次指定候选人简历。
- 需要手动提供候选人姓名。
- 新建 Notebook 时只服务当前单份运行。
- 缺少稳定的默认 Notebook 信息保存机制。
- 缺少对 `data/raw/cv` 批量简历目录的自动处理。

当前实现已经把固定业务资料和临时候选人简历分离：

- `data/sources` 是长期复用的业务背景和岗位需求资料，应常驻默认 Notebook。
- `data/raw/cv` 是待处理的候选人简历队列，每次只上传一份，处理后从 Notebook 中删除。

这样可以减少重复上传背景资料的成本，同时降低候选人资料在 NotebookLM 中长期残留的风险。

真实批量执行中曾出现上传超时、聊天超时和历史候选人 source 残留问题。因此当前实现还要求：

- 每个候选人的 profile 抽取必须只绑定当前候选人 CV source。
- 三阶段分析必须绑定默认长期 sources 加当前候选人 CV source。
- 每个候选人处理前先清理 Notebook 中未记录为默认长期 source 的遗留 sources。
- 三阶段 prompt 在一个 session 中连续提出，但不同候选人之间重新开始新的 chat session。

## 目标行为

### 默认 Notebook 初始化

系统需要支持创建或复用默认 Notebook。

当默认 Notebook 配置不存在时：

- 创建一个新的 NotebookLM Notebook。
- Notebook 标题应表达用途，例如 `CV Checker Interview Batch`。
- 遍历 `data/sources` 下的非隐藏普通文件。
- 将这些文件逐个上传为 Notebook sources，并等待上传完成。
- 保存 Notebook 信息到 `config/default_notebook.yaml`。

当默认 Notebook 配置已存在时：

- 默认复用配置中的 Notebook id。
- 不重复创建 Notebook。
- 不重复上传已记录的 sources。
- 如果 `data/sources` 中新增了未记录的文件，系统会上传新增文件并更新配置。

显式传入 `--notebook-id` 时：

- 优先使用 CLI 指定的 Notebook。
- 仍允许把该 Notebook 信息保存或更新为默认 Notebook，便于后续未指定 Notebook 时自动复用。

### 默认 Notebook 配置

Notebook 信息应保存到：

```text
config/default_notebook.yaml
```

当前结构：

```yaml
notebook:
  id: "<notebooklm-notebook-id>"
  title: "CV Checker Interview Batch"
source_dir: "data/sources"
sources:
  - path: "data/sources/穿戴式AI决策系统开发指南.md"
    id: "<notebooklm-source-id>"
created_at: "2026-04-27T00:00:00+08:00"
updated_at: "2026-04-27T00:00:00+08:00"
```

配置文件属于运行状态配置，应避免写入敏感候选人内容。只保存 Notebook id、source id、source 路径和时间戳。

### 批量 CV 扫描

默认待处理目录：

```text
data/raw/cv
```

系统应扫描该目录下一级非隐藏普通文件，按文件名稳定排序处理。

不应处理：

- 子目录。
- 隐藏文件。
- 已移动到 `processed` 目录的文件。
- 系统临时文件，例如 `.DS_Store`。

如果目录为空，系统应正常退出，并报告没有待处理 CV。

### 候选人信息提取

批量模式不应要求用户手动传入 `--candidate-name`。

每份 CV 上传到 Notebook 后，应先执行一个前置大模型步骤，从当前简历中提取候选人必要信息。

该步骤必须通过 `source_ids=[candidate_source_id]` 限定只读取当前 CV，不允许让默认长期 sources 或历史候选人 source 参与姓名抽取。

最低要求：

- `candidate_name`：候选人姓名。

可选信息：

- `candidate_role`：候选人角色或岗位标签。
- `summary`：简短候选人画像。
- `confidence`：提取置信度或说明。

如果无法可靠提取 `candidate_name`：

- 不执行后续三阶段 prompt。
- 不移动本地 CV。
- 删除 NotebookLM 中当前 CV source。
- 在批量 manifest 中记录失败原因。

文件名不能作为候选人姓名的首选来源。当前实现允许一个最后兜底规则：如果 NotebookLM 返回空姓名，且文件名第一个分隔 token 是 2 到 4 个中文字符，则使用该 token 作为姓名，并在 `00_candidate_profile.json` 中把 `confidence` 标记为 `filename_fallback`。

### 三阶段工作流执行

候选人信息提取成功后，系统继续执行 `config/interview_workflow.yaml` 中已有的三个 prompt：

- `fit_analysis`
- `information_gaps`
- `interview_template`

执行规则沿用现有三阶段工作流：

- 三个 prompt 必须按顺序执行。
- 后续 prompt 应复用前一步返回的 `conversation_id`。
- prompt 格式化时使用前置步骤得到的 `candidate_name`。
- 如果 `candidate_role` 未提取成功，则使用默认值 `候选人`。
- prompt 调用必须显式传入默认长期 source ids 和当前候选人 source id，避免 Notebook 中其他 sources 参与回答。
- 每个候选人的三个问题在一个 session 中连续提出；下一个候选人重新开始新的 session。
- 每个阶段回答返回后立即写入对应 Markdown，避免后续阶段失败导致已完成内容丢失。

### 输出保存

默认输出根目录：

```text
outputs/interview_batch
```

每个候选人使用独立目录：

```text
outputs/interview_batch/<candidate_name_or_safe_filename>/
```

候选人目录至少包含：

```text
00_candidate_profile.json
01_fit_analysis.md
02_information_gaps.md
03_interview_template.md
```

`00_candidate_profile.json` 保存前置候选人信息提取结果，不保存原始简历全文。

如果候选人姓名重复，应使用稳定后缀避免覆盖已有输出，例如：

```text
outputs/interview_batch/张三/
outputs/interview_batch/张三_2/
```

### Source 删除与本地文件移动

“删除当前简历”定义为删除 NotebookLM 中当前 CV 对应的 source。

本地 CV 文件不直接删除。处理成功后移动到：

```text
data/raw/cv/processed
```

移动规则：

- 只有当三阶段输出全部保存成功，并且 NotebookLM 中当前 CV source 删除成功后，才移动本地文件。
- 如果移动目标中已存在同名文件，应使用稳定后缀避免覆盖。
- 如果任一步失败，本地 CV 保留在 `data/raw/cv`，方便下次重跑。

### 运行 manifest

批量运行应记录 manifest，便于续跑和排查。

当前路径：

```text
outputs/interview_batch/run_manifest.jsonl
```

每处理一份 CV 追加一条记录，字段包括：

- `cv_path`
- `status`
- `candidate_name`
- `candidate_role`
- `notebook_id`
- `candidate_source_id`
- `candidate_source_deleted`
- `output_dir`
- `processed_path`
- `error`
- `failure_stage`
- `started_at`
- `finished_at`

manifest 中不保存原始简历全文。

## CLI 入口

当前批量模式参数：

```bash
uv run python main.py --batch-cv-dir data/raw/cv
```

可选参数：

```bash
--source-dir data/sources
--default-notebook-config config/default_notebook.yaml
--output-dir outputs/interview_batch
--processed-dir data/raw/cv/processed
--notebook-id <id>
--config config/interview_workflow.yaml
```

参数优先级：

1. 显式 `--notebook-id`。
2. `config/default_notebook.yaml` 中保存的 Notebook id。
3. 如果前两者都不存在，则创建新的默认 Notebook。

## 边界与异常

- `data/sources` 为空时，应拒绝创建新的默认 Notebook，并提示缺少业务资料。
- `config/interview_workflow.yaml` 缺少三阶段 prompt 时，应拒绝运行。
- NotebookLM 创建 Notebook、上传 source、提问或删除 source 失败时，应记录失败并继续处理下一份 CV。
- 当前 CV 已上传但后续步骤失败时，应尽量删除该 CV source。
- 删除 CV source 失败时，不移动本地 CV，避免下次运行误以为该候选人已完全处理。
- 三阶段 prompt 失败时，`failure_stage` 应记录具体阶段：`fit_analysis`、`information_gaps` 或 `interview_template`。
- 批量任务不应删除或移动 `data/sources` 文件。
- 批量任务不应提交候选人 CV、NotebookLM 认证数据或生成的敏感报告。

## 测试要求

当前测试优先覆盖不依赖真实 NotebookLM 的单元行为。

已覆盖或应保持覆盖的场景：

- 默认 Notebook 配置不存在时，创建 Notebook、上传 `data/sources`、写入 `config/default_notebook.yaml`。
- 默认 Notebook 配置已存在时，复用 Notebook，不重复创建。
- 批量 CV 目录为空时，正常返回无待处理文件。
- 单份 CV 成功时，完成候选人信息提取、三阶段 prompt、输出保存、source 删除和本地文件移动。
- 单份 CV 失败时，记录失败、保留本地 CV，并继续处理下一份。
- 候选人信息提取失败时，不执行三阶段 prompt，不移动本地 CV。
- 候选人信息提取为空但中文文件名前缀可用时，使用文件名兜底并标记 `filename_fallback`。
- profile 抽取只使用候选人 source，三阶段 prompt 使用默认 sources 加候选人 source。
- 三阶段 prompt 连续复用 `conversation_id`。
- prompt 阶段失败时记录具体 `failure_stage`，并保留本地 CV。
- NotebookLM source 删除失败时，不移动本地 CV。
- 输出目录和 processed 目录存在重名文件时，不覆盖已有文件。

## 验收标准

任务实现完成后，应满足：

- 运行一次命令即可创建或复用默认 Notebook。
- `data/sources` 中的资料作为长期 sources 保留在默认 Notebook 中。
- `data/raw/cv` 中的每份简历被逐份处理，且每次只保留当前 CV source 参与候选人分析。
- 每个候选人都有独立输出目录和完整三阶段报告。
- 处理成功的本地 CV 被移动到 `data/raw/cv/processed`。
- 失败的本地 CV 保留在原目录，manifest 中有可排查的失败记录。
- 未指定 Notebook 时，系统能自动从 `config/default_notebook.yaml` 加载默认 Notebook。
