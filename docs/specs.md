# CV Checker 需求与约束说明

## 项目定位

CV Checker 是一个基于 Google NotebookLM 的命令行工具，用于把候选人简历、岗位/业务需求资料和可配置的审查 prompt 组合起来，生成面向招聘与面试决策的 Markdown 报告。

当前项目服务的核心场景不是通用 ATS 系统，而是“穿戴式 AI 硬件初创公司决策辅助系统”中的人才评估环节：创始人或面试负责人需要判断候选人是否适配具体开发需求，识别简历中的信息缺口与可疑点，并快速生成后续面试模板。

项目遵循仓库级工程上下文文档的思路：Claude Code 通过项目记忆文件保存团队约定，Codex 通过 `AGENTS.md` 读取仓库指令。本文档补充的是产品/功能契约，说明系统应该表现成什么样。

参考：

- Claude Code Memory: https://docs.anthropic.com/en/docs/claude-code/memory
- Codex AGENTS.md Guide: https://developers.openai.com/codex/guides/agents-md

## 要解决的问题

招聘和面试准备中有三个高成本问题：

- 候选人简历需要和真实业务需求逐条比对，人工阅读容易遗漏证据链。
- 创始人或面试官需要识别信息缺口、时间线矛盾、经历夸大等风险，但手工整理成本高。
- 面试问题需要围绕业务需求和候选人材料定制，通用题库无法覆盖具体场景。

CV Checker 的目标是把这些工作转化为可重复的 NotebookLM 工作流：上传资料、按固定 prompt 追问、保存结构化 Markdown 结果，供人类最终判断。

## 面向用户

- 创始人：需要快速判断候选人是否匹配当前公司阶段、技术路线和团队文化。
- 面试负责人：需要从简历中提取可追问点，并生成可执行的面评模板。
- AI 辅助开发者：需要通过 CLI 批量或半自动运行 NotebookLM 审查流程，同时保留 prompt 可配置性。

## 成功标准

一次成功运行应满足以下条件：

- 能使用本地 NotebookLM 登录状态创建客户端。
- 能在已有 notebook 中上传候选人简历，或在缺少 notebook id 时基于需求资料创建新 notebook。
- 能按配置文件中的 prompt 顺序获得 NotebookLM 回答。
- 能把三阶段面试工作流输出为固定命名的 Markdown 文件。
- 默认在工作流结束后尝试删除上传的候选人 source，减少候选人资料残留。
- 能在批处理模式下复用默认 Notebook，逐份处理 `data/raw/cv` 中的候选人简历，并保存运行 manifest。
- 失败时不吞掉核心异常，并尽量执行候选人 source 清理。

## 功能范围

### Legacy 单 prompt 简历审查

命令形式：

```bash
uv run python main.py --notebook-id <id> --cv path/to/cv.pdf
```

行为：

- 读取 `config/prompt.yaml` 或用户通过 `--config` 指定的 YAML。
- 要求配置中存在非空 `review_prompt`。
- 将 `--cv` 指定的简历文件上传到目标 NotebookLM notebook。
- 向 NotebookLM 提交 `review_prompt`。
- 将 NotebookLM 的回答打印到标准输出。

### 三阶段面试工作流

命令形式：

```bash
uv run python main.py \
  --candidate-cv path/to/candidate.pdf \
  --candidate-name <name> \
  --requirement path/to/requirement.md
```

也可以通过 `--notebook-id` 复用已有 notebook：

```bash
uv run python main.py \
  --notebook-id <id> \
  --candidate-cv path/to/candidate.pdf \
  --candidate-name <name>
```

行为：

- 如果没有传入 `--notebook-id`，必须至少传入一个 `--requirement`。
- 如果没有传入 `--notebook-id`，系统创建一个新 NotebookLM notebook，并把需求资料上传为 source。
- 上传候选人简历 source。
- 使用同一个 `conversation_id` 连续运行三类 prompt：
  - `fit_analysis`：候选人与业务需求的适配/不适配证据分析。
  - `information_gaps`：录用决策所需的信息缺口、矛盾点、可疑点。
  - `interview_template`：围绕前两步结论生成面试/面评模板。
- 将结果写入 `--output-dir`，默认目录为 `outputs/interview_review`。

输出文件固定为：

- `01_fit_analysis.md`
- `02_information_gaps.md`
- `03_interview_template.md`

默认行为是在工作流结束后删除候选人 source。传入 `--keep-candidate-source` 时保留该 source。

### 批量简历面试工作流

命令形式：

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

行为：

- 系统优先使用 `--notebook-id` 指定的 NotebookLM notebook。
- 如果未指定 `--notebook-id`，系统读取 `config/default_notebook.yaml` 中保存的 notebook id。
- 如果默认 Notebook 配置不存在，系统创建标题为 `CV Checker Interview Batch` 的 notebook，把 `data/sources` 下一级非隐藏普通文件上传为长期 sources，并保存 notebook 与 source 信息到默认配置。
- 扫描 `data/raw/cv` 下一级非隐藏普通文件，按文件名稳定排序处理。
- 每份 CV 处理前，系统会清理 Notebook 中未记录为默认长期 source 的遗留 sources，避免上一次候选人资料污染当前分析。
- 每次只上传一份候选人简历，先要求 NotebookLM 返回候选人信息 JSON。
- 候选人信息抽取只限定当前候选人 CV source；三阶段面试工作流限定为默认长期 sources 加当前候选人 CV source。
- 成功提取 `candidate_name` 后，继续执行三阶段面试工作流，并把输出写入 `outputs/interview_batch/<candidate_name>/`。
- 如果 NotebookLM 未能返回姓名，但文件名第一个分隔 token 是 2 到 4 个中文字符，系统可把它作为最后兜底姓名，并在 profile 中标记 `confidence` 为 `filename_fallback`。
- 输出目录包含：
  - `00_candidate_profile.json`
  - `01_fit_analysis.md`
  - `02_information_gaps.md`
  - `03_interview_template.md`
- 如果候选人姓名重复，输出目录使用 `_2`、`_3` 等后缀避免覆盖。
- 三阶段输出全部保存且候选人 source 删除成功后，本地 CV 移动到 `data/raw/cv/processed`。
- 任一步失败时，本地 CV 保留在原目录，方便后续重跑。
- 每份 CV 的处理结果追加写入 `outputs/interview_batch/run_manifest.jsonl`，其中包含状态、候选人信息、source 删除状态、输出路径、processed 路径、错误信息和 `failure_stage`。

## 输入与配置

### CLI 输入

- `--notebook-id`：NotebookLM notebook id。legacy 模式必填；三阶段工作流中可选。
- `--cv`：legacy 模式的单份简历路径。
- `--candidate-cv`：三阶段工作流的候选人简历路径。
- `--candidate-name`：三阶段工作流的候选人姓名，和 `--candidate-cv` 同时使用时必填。
- `--candidate-role`：prompt 中使用的候选人角色标签，默认 `候选人`。
- `--requirement`：需求/背景资料路径，可重复传入；创建新 notebook 时必填。
- `--output-dir`：三阶段工作流 Markdown 输出目录。
- `--batch-cv-dir`：批量模式的候选人 CV 待处理目录。
- `--source-dir`：批量模式的长期业务资料目录，默认 `data/sources`。
- `--default-notebook-config`：批量模式默认 Notebook 状态配置路径，默认 `config/default_notebook.yaml`。
- `--processed-dir`：批量模式成功处理后移动本地 CV 的目录，默认 `data/raw/cv/processed`。
- `--config`：自定义 YAML prompt 配置路径。
- `--keep-candidate-source`：保留上传到 NotebookLM 的候选人 source。

### YAML 配置

legacy 模式使用：

- `config/prompt.yaml`
- 字段：`review_prompt`

三阶段工作流使用：

- `config/interview_workflow.yaml`
- 字段：`workflow.prompts.fit_analysis`
- 字段：`workflow.prompts.information_gaps`
- 字段：`workflow.prompts.interview_template`

三阶段工作流的三个 prompt 都必须存在且非空。

批量模式额外使用：

- `config/default_notebook.yaml`
- 字段：`notebook.id`
- 字段：`notebook.title`
- 字段：`source_dir`
- 字段：`sources[].path`
- 字段：`sources[].id`
- 字段：`created_at`
- 字段：`updated_at`

该文件是运行状态配置，只保存 NotebookLM id、source id、source 路径和时间戳，不保存候选人简历原文。

## 行为规则与异常

- 找不到候选人简历、legacy 简历或需求资料时，CLI 应通过参数错误终止。
- legacy 模式缺少 `--notebook-id` 或 `--cv` 时，CLI 应通过参数错误终止。
- 三阶段工作流缺少 `--candidate-name` 时，CLI 应通过参数错误终止。
- 没有 `--notebook-id` 且没有 `--requirement` 时，三阶段工作流应拒绝运行。
- 批量模式找不到 `--batch-cv-dir` 时，CLI 应通过参数错误终止。
- 批量模式在没有显式 notebook id、没有默认 Notebook 配置且找不到 `--source-dir` 时，应拒绝运行。
- 批量模式发现候选人姓名无法提取时，应删除当前 CV source，记录失败 manifest，不执行三阶段 prompt，也不移动本地 CV。
- 批量模式的三阶段 prompt 失败时，manifest 的 `failure_stage` 应记录具体阶段；可能值包括 `add_source`、`profile`、`fit_analysis`、`information_gaps`、`interview_template`、`delete_source`、`move_cv`。
- 批量模式如果候选人 source 删除失败，应记录失败，不移动本地 CV。
- 找不到配置文件时，应抛出 `FileNotFoundError`。
- 三阶段 prompt 配置缺失时，应抛出 `ValueError`。
- NotebookLM 调用失败时，业务异常应向上抛出；如果候选人 source 已上传，应尝试清理。
- 候选人 source 删除失败不应覆盖原始业务异常，结果中应体现删除未成功。

## 非目标

当前项目不负责：

- 初始化 NotebookLM 浏览器登录或认证状态。
- 实现本地简历解析或本地 LLM 推理；候选人信息抽取由 NotebookLM 前置 prompt 完成。
- 替代人类做最终录用决策。
- 验证 NotebookLM 输出中的事实真实性。
- 管理候选人数据库、岗位库或招聘流程状态。
- 提供 Web UI、API Server 或批处理队列。

## 安全与隐私约束

- 不应提交候选人 CV 文件、浏览器 session、NotebookLM 认证数据或生成的敏感报告。
- `.notebooklm/` 应继续作为本地认证数据目录被忽略。
- `data/raw/` 中的 NotebookLM 会话导出只能作为开发样例和历史上下文，不应在公开文档中复制候选人隐私细节。
- prompt 应保存在 `config/`，通过 `--config` 覆盖，不应把审查规则硬编码到 Python 逻辑中。
