# 轨迹格式与产物

本地常见的轨迹有几种来源，字段差别很大。开始数步骤之前先确定用哪一种，在输出里写明。`scripts/list_calls.py` 能自动识别 ATIF、含 Claude tool_use/tool_result 事件的平台 normalized.json，以及 Codex exec 的 codex.txt。平台封装的 Codex normalized.json 请改用同 run 的 ATIF 或原生 codex.txt。

## 0. 同一个 run，不同来源数出的步数可能不同

- Codex 旧版本的 ATIF 里，一个 `exec` 调用可以包含多个 `exec_command` 和 `write_stdin`，另外还有单独的 `sleep` 调用，所以 ATIF 的调用数和 codex.txt 的 `command_execution` 数通常对不上。
- codex.txt 的 `file_change` 和 `command_execution` 是两类条目，计数方式也和 ATIF 不同。
- 平台 normalized.json 和同一 run 的 ATIF（Claude Code）一般能对上，可以用 `usage` 记录里的 `tool_calls` 总数核对。

同一个 run 的多份分析，以及多个 run 之间的对比，必须用同一种来源计步。引用已有人工文档里的 step 号之前，先确认它用的是哪种来源。

## 1. 平台 Pipeline 导出：`trajectory/normalized.json`

位置：`extracted/batch-*/tasks/TASK-*/runs/run-*/trajectory/normalized.json`。

- 顶层是一个列表，每个元素是 `{seq, role, name, content, stage_id, status, time}`。`seq` 是原始事件编号。
- `role` 的取值：
  - `stage`：流程阶段，例如“Agent 作答”“运行独立 Verify”；
  - `user`/`任务输入`：题面原文；
  - `event`/`claude`：Claude Code 的 stream-json 事件，`content` 是一段 **JSON 字符串**，要再解析一次；
  - `usage`：用量。最后一条 `运行消耗` 里有 `tool_calls`、`rounds`、`tool_failures` 的总数，可以用来核对计数；
  - `tool`/`independent_verifier`：独立验证的结果（`passed`、`score`、各维度的 `metrics`）。
- `event` 内层的 `type`：
  - `assistant` 消息的 `content` 中含 `tool_use`、`thinking`、`text`；
  - `user` 消息中含 `tool_result`。`tool_result` 的文本也是 JSON：`{exit_code, stdout, stderr}`，出错时另有 `is_error`；
  - **上下文压缩**：`system`/`compact_boundary`（`compact_metadata` 里有 `pre_tokens`、`post_tokens`），紧接着是一条 `user` 文本 “This session is being continued from a previous conversation…”，后面就是压缩摘要。这条摘要说明了压缩后的 agent 还“记得”什么，是重要证据；
  - 其他 `system`（`thinking_tokens`、`status`、`api_retry`）、`stream_event:*` 和 `tool_progress` 是噪音。
- agent 往往只有 `mcp__taskbox__workspace_exec` 一个工具（见 `system/init` 里的 `tools`），所有读和写都是 shell 命令，不会有 Read/Edit 工具的记录。
- 同目录下的 `trajectory-agent.jsonl`、`trajectory-all.jsonl` 是外面包了一层 `{"type":"event","event":{...}}` 的同类事件；`trajectory-readable.txt` 是可读版本。

## 2. Harbor ATIF：`trial/agent/trajectory.json`

- 顶层：`{schema_version, session_id, agent{name, version, model_name}, steps[], final_metrics}`。
- 每个 step：`{step_id, timestamp, source(system/user/agent), message, reasoning_content, tool_calls[], observation{results[]}, metrics}`。`tool_calls[i]` 的结构是 `{tool_call_id, function_name, arguments}`，通过 `observation.results[j].source_call_id` 与结果配对。
- 一个 step 可以有多个 tool_call，每个调用各算一步，原始编号写成 `step_id`。`reasoning_content` 是 thinking（Codex 里是推理摘要），只能写成高层摘要。
- **Claude Code**：工具名是 `Bash`、`Read`、`Edit`、`Write` 等。上下文压缩表现为一个 `source=user` 的 step，消息以 “This session is being continued…” 开头。
- **Codex，旧版本**（如 0.154）：工具名是 `exec` 和 `sleep`。`exec` 的参数是一段 JS，例如 `text(await tools.exec_command({cmd:"…", max_output_tokens:N}))`，一个 `exec` 里可以有多个 `exec_command` 或 `write_stdin`。结果是一段 Python repr 列表，里面嵌着 JSON 块（`exit_code`、`original_token_count`、`output`）。`list_calls.py` 会把命令和输出解出来。
- **Codex，新版本**（如 0.157）：工具名是 `exec_command`（参数 `{cmd, max_output_tokens, yield_time_ms}`）、`write_stdin`（用来轮询后台会话）和 `web_search_call`（没有 observation）。
- 同一 trial 目录下还有原生日志：`agent/claude-code.txt`、`agent/codex.txt`、`agent/sessions/`（Codex 的 `rollout-*.jsonl` 里有 `function_call` / `function_call_output`，计数与 ATIF 一致）。

## 3. Codex 原生 JSONL：`agent/codex.txt`

- 每行一个事件，看的是 `item.started` 和 `item.completed`。`item.type` 有 `command_execution`（`command`、`aggregated_output`、`exit_code`）、`file_change`、`agent_message`、`reasoning`、`web_search`。
- **按 `item.started` 的顺序编号。** `item.completed` 的先后是完成顺序：后台命令会晚很多才完成，按它编号会把顺序搞乱。`list_calls.py` 已经按发起顺序处理。
- **不要用 codex.txt 搜索写入。** `file_change` 只记录 `{path, kind}`，没有补丁内容；heredoc 写入能在 `command` 里看到，`apply_patch` 的内容却不一定能看到。搜索写入用 ATIF 或 `rollout-*.jsonl`，codex.txt 只用来对照。

## 4. 派生文件不是原始材料

run 目录和导出目录里常有派生文件：译文（`normalized.zh-CN.json`、`thinking_*.zh.*`）、复核文件（`*.review.*`、`review/`、`review-summary.json`、`calibration/`）、manifest 的 `note` 字段、人工写的 README 和 notes。这些文件可能已经写有人工结论。可以用来对照，但不能当作原始证据；盲测时要排除。

## 5. 验收产物的几种布局

先认出是哪种布局，再找“是否通过”和“哪些检查失败”。

| 布局 | 是否通过看哪里 | 失败项与证据 | 最终代码 |
|-|-|-|-|
| **Harbor trial**（`trial/verifier/`） | `reward.json` 的 `reward`（0/1，要求全部检查通过，所以 `total` 为 0.997 时 `reward` 仍可能是 0）；`result.json` 的 `verifier_result.rewards`、`exception_info` | `report.json`：`dimensions`、`failed_checks`（按维度列出）、`gate`；`verifier/work/*.json`（tests、requirements、functional、ci_gates）；`*_ok.txt` 这类派生结果；`*.log`；`androidTest-results-*.tgz`（JUnit 执行顺序、逐用例 logcat、进程 PID） | `agent.patch` |
| **Pipeline verifier**（runs 下的 `verifier/`） | `report.json` 的 `accepted`、`status`；`pipeline-result.json` 的 `passed`/`score`。注意 `score` 可能是 0.0，而 `metrics.total` 只是诊断用的完成度 | `report.json` 的 `checks` 列表、`checks.json`、`junit/`、`tests.log`、`screens/*.txt`（Compose 语义树的文本，不是截图） | 常常没有补丁。用 `source-manifest.json` 的逐文件哈希，加上 `source-review.json`；需要时按轨迹重放写入，再用哈希核对 |
| **Android Bench v4**（`runs/*/verifier/`） | `detailed_scores.json` 的 `status`（如 `AGENT_FAILED_VALIDATION`）和 `score`。`reward.txt` 里是 `partial_score`，**不是 0/1** | `validate_results.txt`（gate、penalty、visual 等）、`detailed_scores.json` 的 `diagnostics`、`multipliers_applied`、`scoring_breakdown`；`visual_score.json`（逐张截图的评委判定、分数和理由）；`visual/actual/*.png`；`test-stdout.txt` | `_submission.patch` |
| **平台 normalized.json** | 最后一条 `tool`/`independent_verifier` 的 `passed`、`score` | 同 run 目录下的 `evidence/artifacts.tar.gz` 解开后，就是 Harbor trial 布局 | 同上 |
| **交付集导出** | `delivery-export/runs/<题>/<模型>/trial-NN/score.json` | 同目录的 `verifier/` | 同目录 |

其他排查 X2 时要用到的材料：

- verifier 脚本（如 `tests/test.sh`）：派生检查项是怎么算的，判分前会恢复哪些受保护的文件（如 `tests/baseline/protected_files.txt`）；
- 不同版本的 fixture（如 `task/tests-other-versions/`），比较它们的 diff；
- no-op 基线 run、原始 HEAD 对照记录；
- 重评前后的截图哈希。

## 6. 在命令和结果里识别读取、写入和截断

| 动作 | 常见形式 | 要记下的内容 |
|-|-|-|
| 读取 | `cat`、`sed -n 'a,bp'`、`head -n`、`tail`、`rg -n`/`grep -n`（只看到匹配行）、`git show REV:path`、`javap`；Read 工具的 offset/limit | 文件和**实际范围**；有没有被 `head` 或工具截断；几个文件是否合在一条命令里读（更容易被截断） |
| 写入 | `cat > f <<'EOF'`、`tee`、`sed -i`、`perl -pi`、`python - <<'EOF'`（`open(p,'w')`、`write_text`）、`apply_patch`、`git apply`；Edit/Write 工具 | 改了哪些文件；脚本中途是否抛异常；替换有没有真正命中；同一个文件是否“先以写模式打开、后读取” |
| 回退或覆盖 | `git checkout -- f`、`git restore`、`git stash`、重新生成、格式化（如 spotless） | 之前的修改是否被抹掉 |
| 后台任务 | `… &`、`nohup`，之后接 `sleep`；Codex 的结果里有 “Process running with session ID N”，之后用 `write_stdin` 或读日志取结果；Claude Code 的 `run_in_background` | 后来有没有读到最终结果；结果可能出现在后面某次调用的输出里 |

**截断标记**：

- Codex：结果开头的 `Warning: truncated output (original token count: N)`，正文中间的 `…N chars truncated…` 或 `…N tokens truncated…`（会保留头尾）。截断多少由那次调用的 `max_output_tokens` 决定。
- Claude Code：`<persisted-output>`、`Output too large`，后面跟着完整输出的保存路径。
- 命令自己的截断：`| head -60`、`| tail -50`。

一条命令里可以同时有多次读写，用 `;` 或 `&&` 连接，整条命令的退出码只反映最后一段。

## 7. 工具调用列表脚本

`scripts/list_calls.py`，用 `python3 -I` 运行，只读轨迹，不执行其中任何内容：

```bash
python3 -I list_calls.py <轨迹> [-p 参数正则] [-r 结果正则] [-s N 或 N-M]
```

- 每行输出 `序号 原始编号 工具 标记 参数摘要 结果摘要`。标记包括 `exit=N`、`TRUNC`、`ERR`（Traceback、构建失败、编译器 `e:` 行）和 `BG`。压缩边界单独一行，显示为 `[COMPACT]`。
- `-p` 在**解码后及原始**参数里匹配：引号和换行都还原了，Codex 的 `exec` 包装也已解开，所以正则按原文写就行，例如 `open\(p, 'w'\)`。
- `-r` 在结果里匹配，可以用来查某段关键内容有没有进过上下文。
- `-s` 打印原始 JSON 参数、用于导航的解码视图及文本结果。Codex exec 中包含的补丁保留在原始参数中，不能只看解码出的 shell。
- 匹配到不等于执行到，也不等于造成了损坏：要用 `-s` 看结果，确认那次写入真的生效了。搜不到片段也不代表没写过：内容可能是脚本拼出来的、模板生成的或者格式化工具改的，这时改为查所有写过这个路径的调用。
