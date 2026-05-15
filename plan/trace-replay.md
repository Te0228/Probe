# Goal-Driven(1 master agent + 1 subagent) System

Here we define a goal-driven multi-agent system for solving any problem.

Goal: [[[[[
在 Probe 中实现 `probe replay <session_id>` 命令——读取一个已经存在的 `probe_traces/<session_id>/trace.jsonl`，只重跑 `analyse → conclude → fix-generation` 这后半段流程，跳过 `observe / hypothesize / instrument / execute`，不重新启动 debugpy，不重新跑测试，生成一份新的 HTML/JSON/Markdown report（命名 `report-replay-<ISO8601_TIMESTAMP>.html`，其中 timestamp 形如 `20260515T103011Z`——无分隔符紧凑格式，便于 `ls` 自然按时间排序）。

**本版本范围限定为 analyse 阶段之后的重跑**。核心用途是：当用户修改 `hypothesis.py` 里的 `_heuristic_analyse` 评分权重 / confirm 阈值时，能直接对历史 trace 重新评估，看 verdict 是否会变。**注意 hypothesize 重跑（用于 SYSTEM_PROMPT iteration）作为 v2 能力**，需先验证 `trace.jsonl` 是否完整捕获 source_code 上下文，本版本不实现。

Replay 必须**不污染原 trace**：在 `probe_traces/<session_id>/` 下写入独立的 `trace-replay-<timestamp>.jsonl`，与原 `trace.jsonl` 共存且不修改原文件。

LLM backend（DeepSeek / Anthropic）不可用时，fix-generation 阶段**降级到 heuristic patch**（复用现有 `FixGenerator._heuristic_patch`），replay 整体仍判定成功（exit 0）。

需要新增 `src/probe/replay.py` 模块、相应 CLI 子命令、以及在 tracer 里支持 "replay" 标记的事件类型。
]]]]]

Criteria for success: [[[[[
(1) 命令行 `probe replay <session_id>` 在合法 session_id 下完成执行并 exit 0；session_id 不存在时 exit 2 并打印明确错误（区别于通用异常的 exit 1）；

(2) `replay` 模块不引入任何 DAP 依赖，双层验证：
    - 静态：`grep -E "^(from|import).*(debugpy|probe\.dap)" src/probe/replay.py` 输出为空
    - 运行时：`python -c "import probe.replay; import sys; assert 'debugpy' not in sys.modules and 'probe.dap.client' not in sys.modules"` 必须 exit 0

(3) replay 产物文件名包含 `replay` 关键字 + ISO 8601 紧凑时间戳（regex `replay-\d{8}T\d{6}Z`），与原 `report.html` 共存不覆盖；

(4) Replay 写入**独立**的 `trace-replay-<timestamp>.jsonl` 到原 session 目录，**不修改原 `trace.jsonl`**（运行前后对原 `trace.jsonl` 取 MD5/SHA-256 必须一致）；该新 jsonl 中每个 event 的 `data` 字段里有 `"replay": true` 标记；

(5) **Forward 一致性测试**：用 fixture `type_mismatch` 跑一次原始 debug，然后在 `_heuristic_analyse` 代码未修改的前提下 replay 同一个 session_id，replay 产物的 verdict 与原始一致；

(6) **Differential 测试**：在 replay 前临时把 `_heuristic_analyse` 中 confirm 阈值 `>= 0.5` 改成 `>= 99.0`，replay 同一个 session_id，verdict 必须从 `confirmed` 变为 `inconclusive`（证明 replay 真的重跑了评估逻辑，且扰动来自代码而非数据）；

(7) 测试套件总数 ≥ 53，原有 51 个测试一个都不删；新增至少 2 个测试分别覆盖 (5) 和 (6) 两个场景；

(8) README 同时改两处：从 Roadmap section 删除 "Trace replay" 那行，并在 Status 表中新增一行标记 Implemented；

(9) LLM backend 不可用（网络断、API key 缺失、quota 耗尽）时，replay 整体仍 exit 0，fix-generation 阶段降级到 `_heuristic_patch`，replay HTML 报告里明确标注 "fix generated via heuristic fallback (LLM unavailable)"。
]]]]]

Here is the System: The system contains a master agent and a subagent. You are the master agent, and you need to create 1 subagent to help you complete the task.

## Subagent's description:

The subagent's goal is to complete the task assigned by the master agent. The goal defined above is the final and the only goal for the subagent. The subagent should have the ability to break down the task into smaller sub-tasks, and assign the sub-tasks to itself or other subagents if necessary. The subagent should also have the ability to monitor the progress of each sub-task and update the master agent accordingly. The subagent should continue to work on the task until the criteria for success are met.

## Master agent's description:

The master agent is responsible for overseeing the entire process and ensuring that the subagent is working towards the goal. The only 3 tasks that the main agent need to do are:

1. Create subagents to complete the task.
2. If the subagent finishes the task successfully or fails to complete the task, the master agent should evaluate the result by checking the criteria for success. If the criteria for success are met, the master agent should stop all subagents and end the process. If the criteria for success are not met, the master agent should ask the subagent to continue working on the task until the criteria for success are met.
3. The master agent should check the activities of each subagent for every 5 minutes, and if the subagent is inactive, please check if the current goal is reached and verify the status. If the goal is not reached, restart a new subagent with the same name to replace the inactive subagent. The new subagent should continue to work on the task and update the master agent accordingly.
4. This process should continue until the criteria for success are met. DO NOT STOP THE AGENTS UNTIL THE USER STOPS THEM MANUALLY FROM OUTSIDE.

## Basic design of the goal-driven double agent system in pseudocode:

create a subagent to complete the goal

while (criteria are not met) {
  check the activty of the subagent every 5 minutes
  if (the subagent is inactive or declares that it has reached the goal) {
    check if the current goal is reached and verify the status
    if (criteria are not met) {
      restart a new subagent with the same name to replace the inactive subagent
    }
    else {
      stop all subagents and end the process
    }
  }
}
