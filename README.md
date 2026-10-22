# Trajectory Analysis

An evidence-based skill for analyzing coding-agent trajectories with Codex or Claude Code.

用原始轨迹、工具返回、最终补丁和验收结果，还原 AI 编程 agent 的工作过程，定位错误首次引入的位置。

## 能做什么

- **过程解读**：按连续调用区间划分阶段，列出实际工具名、代表性参数和返回证据。
- **消息与决策**：保留可见 assistant 消息；对 reasoning 字段仅给简短高层摘要，标明原始事件编号。
- **失败归因**：从失败用例倒推代码、首次写入及当时取得的信息，区分模型、题目、评测器和基础设施因素。
- **问题状态**：正常推进、已恢复、最终残留、验证受限和待确认分别报告。同一问题跨阶段只计一次。

默认阶段表：

| 步骤区间 | 阶段与决策摘要 | 主要工具调用及参数 | 可见消息／思考摘要 | 返回结果与证据 | 问题与状态 |
|---|---|---|---|---|---|
| #1 | 调查当前配置 | `Bash(command="sed -n '1,80p' src/client.py")` | assistant 消息·step 2：先检查超时配置 | 返回 `DEFAULT_TIMEOUT = 0` | 正常调查 |
| #2–3 | 修改并复验 | `Edit(file_path="src/client.py", …)`；`Bash(command="pytest -q tests/test_client.py")` | assistant 消息·step 3：修正配置后运行相关测试 | 编辑成功，`1 passed` | 已恢复：超时配置问题 |
| #3之后 | 最终答复 | 无工具调用 | assistant 消息·step 5：报告修改与测试结果 | 与实际测试结果分别核对 | 正常收尾 |

这是合成示例，不代表真实模型或运行成绩。`#N` 指工具调用序号，`step_id/seq` 是原始事件编号；没有工具调用的消息不额外计一步。一次外层 `exec` 可能包含多个操作，因此不同工具包装方式的调用总数不能直接比较效率。

## 安装

仓库根目录就是一个 skill。已有同名目录时先自行备份。

Codex：

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
git clone https://github.com/jarad-z/trajectory-analysis.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/trajectory-analysis"
```

Claude Code：

```sh
mkdir -p "$HOME/.claude/skills"
git clone https://github.com/jarad-z/trajectory-analysis.git \
  "$HOME/.claude/skills/trajectory-analysis"
```

## 使用

在能访问本地运行产物的会话中指定轨迹，例如：

> 用 trajectory-analysis 分析这个 run。给出连续阶段表，写出具体工具调用、assistant 消息／思考摘要、返回证据和问题状态。定位最终失败根因，把已修复的中途错误单独列出。

提供原始轨迹及可用的题面、补丁、verifier 报告和测试日志。不要将已经写有人工结论的 README、译文或 review 作为原始证据。

使用 `U1` 一类编号时先解释具体问题；它不是错误分类代码。同一问题经历引入、发现、修复、复验和交付，仍只算一个问题。

## 工具调用索引

`scripts/list_calls.py` 只依赖 Python 标准库，只读取文件，不执行轨迹中的命令。

支持：

- Harbor ATIF `trajectory.json`，包括 Claude Code 和 Codex 的调用。
- 含 Claude `tool_use/tool_result` 事件的平台 `normalized.json`。
- Codex exec 的原生 JSONL（通常名为 `codex.txt`），按 `item.started` 顺序编号。

平台封装的 Codex `normalized.json` 尚不支持，请使用同 run 的 ATIF 或原生 `codex.txt`。Codex session rollout JSONL 与 exec 输出不是同一种格式，不应混用。

```sh
python3 -I scripts/list_calls.py examples/minimal.atif.json
python3 -I scripts/list_calls.py examples/minimal.atif.json -p timeout
python3 -I scripts/list_calls.py examples/minimal.atif.json -s 1-3
```

`-p` 同时搜索解码参数和原始参数；`-r` 搜索结果；`-s` 显示原始 JSON 参数、导航用的解码视图和文本结果。原始参数保留 Codex `exec` 内的补丁。

标记包括 `exit=N`、`TRUNC`、`ERR`、`BG`、`[COMPACT]`；它们是导航线索，不是自动归因。脚本只列工具调用和压缩边界；消息与 reasoning 字段仍需回到原始轨迹阅读。

## 文件

- [SKILL.md](SKILL.md)：入口、证据规则和两种工作流。
- [格式说明](references/trajectory-formats.md)：计步、调用配对和 verifier 布局。
- [失败分类](references/failure-taxonomy.md)：阶段分类、验证标签、责任方和置信度。
- [输出模板](references/output-templates.md)：阶段表、问题状态和归因卡。
- [合成案例](references/cases.md)：机制示例，不含真实运行数据。
- [最小轨迹](examples/minimal.atif.json)：可运行的合成输入。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

测试覆盖三种输入格式、发起顺序、结果配对、压缩与错误标记，以及 Codex 包装调用的补丁展示和搜索。

公开版不包含私人路径、内部文档标识、真实运行轨迹或个人校准结论。真实数据的分析结果不会自动成为仓库内容。

## 参考与许可证

分类细化参考 [LoLBench](https://arxiv.org/abs/2609.37143) 的失败模式讨论；本项目框架不等同于论文分类，也不是统计测量结果。

[MIT](LICENSE) © 2026 Jarad-z。
