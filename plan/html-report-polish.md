# Goal-Driven(1 master agent + 1 subagent) System

Here we define a goal-driven multi-agent system for solving any problem.

Goal: [[[[[
将 `probe_traces/<session_id>/report.html` 升级为面试 screen-share 级别的可视化产物，具体地：

(1) **Decision tree 段落**：从当前文字列表改为真正的视觉树形图。结构约定：
    - 纵向轴 = iteration（iteration 0 在上方，iteration N 在下方）
    - 同一 iteration 内的多个 hypothesis 横向并列
    - 每个 hypothesis 节点下方挂接对应的 evidence 列表作为子节点
    - 节点状态颜色编码：refuted = 红 `×`、confirmed = 绿 `✓`、inconclusive = 灰 `—`
    - 节点间用 `<svg><line>` 或 CSS border 渲染父子连线
    - 整体 DOM 必须由 `<svg>` 元素或带 `class="tree-node"` 的嵌套 div 构成，禁止用 `<ul><li>` 列表

(2) **Python syntax highlighting**：必须覆盖以下三处位置，且统一用内联 CSS class（如 `class="kw"`、`class="str"`、`class="cmt"`、`class="num"`、`class="op"`）：
    - Patch Review section 的 diff 区块（diff 加/减行底色 + Python token 着色双重叠加）
    - Evidence Gallery 中 stack frame 引用的源码行
    - Hypothesis section 中 verification_plan 里的 `expression` 字符串
    - 禁止引入任何外部 JS / CDN / web font

(3) **Timeline section**：
    - 每个 step block 点击 expand/collapse，带 200ms CSS transition
    - 按 timestamp 排序后的**最后一个 step** 有独特视觉标识（外发光或加粗边框）
    - hover 任意 step block 显示 tooltip：该 step 的 elapsed time（毫秒）+ event_id 前 8 位

(4) **Self-contained 约束**：
    - 单 HTML 文件，无任何 `<link>` / `<script src>` / `<img src>` 引用 `http(s)://` URL
    - 不引入 web font、不引入图标库
    - **允许 inline `<script>` 和 inline `<style>`**（expand/collapse、tooltip 定位、syntax highlight class 都要它们）；只是禁止任何 `src=` / `href=` 指向外部资源

(5) **现有 section 不倒退**：Hypotheses 列表、Evidence Gallery、Patch Review、Timeline 这四个核心 section 在新版 report 中必须保留，字段不能丢失或被截断。
]]]]]

Criteria for success: [[[[[
(1) **Baseline 先落盘**：在动手前，对当前任意一个 fixture session 的 `report.html` 记录 polish 前基线，落盘到 `plan/html-report-polish.baseline.md`，包含三个字段：
    - `wc -c report.html` 的字节数
    - `gzip -c report.html | wc -c` 的字节数
    - 当前 decision tree、syntax highlighting、timeline 的截图各一张

(2) **Browser compatibility**：5 份 fixture report.html 在 Chrome 最新版离线（断网）渲染无 console error。验证命令：
    ```bash
    for f in probe_traces/*/report.html; do
      chrome --headless --disable-gpu --dump-dom "file://$PWD/$f" 2>&1 | grep -iE "error|warning" && exit 1
    done
    ```
    或用 `playwright` 脚本捕获 `page.on("pageerror")` 和 `page.on("console", level="error")`，断言均为零事件。

(3) **Decision tree DOM 硬验证**：
    - `grep -cE '(<svg|class="tree-node")' report.html` ≥ 1
    - decision tree section 内禁止出现 `<ul>` / `<li>`：`xmllint --xpath "//*[@id='decision-tree']//ul" report.html` 必须报 "not found"

(4) **Syntax highlighting class 硬验证**：
    - `grep -cE 'class="(kw|str|cmt|num|op)"' report.html` ≥ 10（多种 token、多次出现）
    - `grep -oE 'class="(kw|str|cmt|num|op)"' report.html | sort -u | wc -l` ≥ 3（至少 3 种不同 class）

(5) **Size budget**（相对 baseline）：
    - 未压缩：`wc -c report.html` ≤ `baseline_uncompressed * 2.0`
    - 压缩后：`gzip -c report.html | wc -c` ≤ 51200（50 KB 硬上限）

(6) **No external network 双重验证**：
    - `grep -E "https?://" report.html` 必须无输出
    - `grep -E "<(link|script|img|iframe)[^>]+(src|href)" report.html | grep -v "data:"` 必须无输出（所有引用必须是 data URI 或 inline）

(7) **自动化测试**：新增 `tests/test_html_report.py`，对每个 fixture 跑完后断言：
    - `report.html` 文件存在且大小 > 5KB
    - 包含 `<svg` 或 `class="tree-node"`
    - 至少 3 种 syntax highlighting class 出现
    - 不包含 `https?://` URL（除非位于 `<script>` 或 `<style>` 内字符串字面量中）
    - 必须包含字符串 "Hypotheses"、"Evidence"、"Patch"、"Timeline"（保证四个 section 仍存在）
    - 现有 51 个测试 + 新增测试全部通过

(8) **Visual evidence 截图**：以下三张图必须存在于 repo：
    - `docs/screenshots/report-decision-tree.png` — 含 decision tree section，至少可见 2 个 hypothesis 节点 + 红/绿/灰三色之中至少 2 色
    - `docs/screenshots/report-syntax-highlight.png` — 含一段 patch diff，diff 加/减底色与 Python token 着色同时可见
    - `docs/screenshots/report-timeline.png` — 含 timeline 的彩色 step block 序列
    - README 在 "Demo projects" section 下方插入这三张截图

(9) **Backward compatibility**：用现有 `probe_traces/<已存在 session_id>/trace.jsonl` 调用 `Tracer.build_html_report()`（或等价 API），必须能生成新版 report.html 而无需重跑 debuggee；老 trace 不需要 schema 迁移。

(10) **失败回滚**：如果 5 份 fixture 中任意一份 report.html 生成失败或验证 (2)-(7) 任一条不通过，**整个改动不准 commit**——不允许 "4 份能渲染、1 份挂掉" 这种半成品状态留在 git 历史里。
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
