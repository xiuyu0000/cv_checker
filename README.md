# CV Checker

CV Checker 是一个基于 Google NotebookLM 的命令行工具，用于简历审查、三阶段面试材料生成和批量候选人处理。它负责上传资料、编排 prompt、保存 Markdown/JSONL 输出；候选人判断和录用决策仍需人工完成。

## 适用场景

- 快速对单份 CV 做 legacy 单 prompt 审查。
- 将候选人 CV 与岗位/业务需求资料放入同一个 NotebookLM notebook，生成适配分析、信息缺口和面试模板。
- 批量处理 `data/raw/cv` 中的候选人简历，复用默认 notebook 和长期业务 sources。

本项目不是 ATS、Web 服务或本地 LLM/RAG 系统，也不负责初始化 NotebookLM 登录状态。

## 环境准备

项目要求 Python `>=3.14`，并使用 `uv` 管理依赖：

```bash
uv sync
```

NotebookLM 客户端通过 `NotebookLMClient.from_storage()` 读取本地认证状态。运行 CLI 前，请先确保本机已有可用的 NotebookLM 浏览器/session 配置。

## 快速开始

Legacy 单 prompt 简历审查：

```bash
uv run python main.py --notebook-id <id> --cv path/to/cv.pdf
```

单候选人三阶段面试工作流，新建 notebook 并上传需求资料：

```bash
uv run python main.py \
  --candidate-cv path/to/candidate.pdf \
  --candidate-name <name> \
  --requirement path/to/requirement.md
```

单候选人三阶段面试工作流，复用已有 notebook：

```bash
uv run python main.py \
  --notebook-id <id> \
  --candidate-cv path/to/candidate.pdf \
  --candidate-name <name>
```

批量简历面试工作流：

```bash
uv run python main.py --batch-cv-dir data/raw/cv
```

## 配置

- `config/prompt.yaml`：legacy 模式使用，必须包含非空 `review_prompt`。
- `config/interview_workflow.yaml`：单候选人和批量三阶段工作流使用，必须包含 `workflow.prompts.fit_analysis`、`workflow.prompts.information_gaps`、`workflow.prompts.interview_template`。
- `config/default_notebook.yaml`：批量模式的默认 NotebookLM 运行状态，保存 notebook id、source id、source 路径和时间戳。

`--config` 在 legacy 模式下指向单 prompt 配置，在三阶段和批量模式下指向 workflow 配置。

## 输出

单候选人工作流默认输出到 `outputs/interview_review`：

- `01_fit_analysis.md`
- `02_information_gaps.md`
- `03_interview_template.md`

批量工作流默认输出到 `outputs/interview_batch`。每个候选人目录包含：

- `00_candidate_profile.json`
- `01_fit_analysis.md`
- `02_information_gaps.md`
- `03_interview_template.md`

批量运行还会追加写入 `outputs/interview_batch/run_manifest.jsonl`。成功处理且候选人 source 删除完成后，本地 CV 会移动到 `data/raw/cv/processed`。

## 开发与测试

当前离线测试使用标准库 `unittest`，异步测试使用 `unittest.IsolatedAsyncioTestCase`。运行测试：

```bash
PYTHONPATH=. uv run python -m unittest discover -s tests -p 'test_*.py'
```

轻量语法检查：

```bash
uv run python -m compileall main.py src
```

## 文档索引

- `docs/specs.md`：用户可见功能契约、CLI 参数、输出和异常行为。
- `docs/design.md`：模块职责、数据流、错误清理策略和测试设计。
- `docs/task.md`：批量工作流任务契约和验收说明。
- `AGENTS.md`：面向代码代理和贡献者的仓库规则。

## 安全与隐私

不要提交候选人 CV、NotebookLM 浏览器 session、认证数据、生成报告或其他敏感 artifacts。`config/default_notebook.yaml` 是运行状态文件，可能包含本机绝对路径和 NotebookLM source id；协作时应避免把真实候选人资料或私密环境信息写入公开文档。

工作流默认会在结束后尝试删除候选人 source，但 NotebookLM 输出仍可能包含敏感信息，分享和归档前需要人工复核。
