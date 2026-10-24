---
name: game-skill
description: >
  Codex/Claude 共用的 Game 理论知识库查询与增量维护 Skill。用于回答 Mikey、Tankman、
  连招流、PUA 历史、女生类型、搭讪、约会、聊天、框架等问题；也用于把视频、音频、
  PDF、EPUB、DOCX、图片、Markdown、字幕、JSON 或目录加入知识库，恢复处理中断，
  生成逐来源结论，增量更新模块、全文与向量索引、FULL.md，以及在用户明确说“更新并发布”
  时同步 GitHub Release、Google Drive、Codex 和 Claude。不要对观点做立场筛选、删减或
  强制对齐；矛盾观点按来源并列保留，无法归类时创建新模块。
---

# game-skill v4

这是一个薄入口。仓库是控制层，默认数据根目录是 `/Users/liyongsheng/Documents/Game知识库`。
不要把 `FULL.md` 整体加载进上下文；用 `scripts/game-kb` 检索、精读和增量处理。

## 意图路由

- 查询理论、案例、原文或比较流派：先读 `skill.yaml`，按触发词加载一到多个 `modules/*.md`；需要完整证据时运行 `scripts/game-kb search "<query>" --top-k 8`，再运行 `scripts/game-kb read <chunk-id>`。
- 加入单个文件或目录：运行 `scripts/game-kb update <path>`，然后处理下述 Agent 蒸馏队列。
- “处理 inbox”：运行 `scripts/game-kb update /Users/liyongsheng/Documents/Game知识库/inbox`。
- 查看或恢复：运行 `scripts/game-kb status`，再继续 `next-job`；不要重头处理已接受任务。
- “更新并发布”：运行 `scripts/game-kb update <path> --publish`。只有这个明确意图或直接 `publish` 命令才允许外部写入。
- 仅验证或构建：分别运行 `scripts/game-kb verify --full`、`scripts/game-kb build-full`。

完整协议、命令和故障恢复见 `KB_REFERENCE.md`。

## Agent 蒸馏循环

资料确定性提取完成后，循环执行：

1. 运行 `scripts/game-kb next-job --max-chars 12000`。
2. 如果返回 `status: empty`，结束。
3. 只基于返回片段生成符合 `schemas/claim.schema.json` 的 JSON：
   - `claim_type` 仅表示证据关系：`direct`、`reported`、`inference`；
   - `quote` 必须逐字出现在片段中；
   - 已有模块自然容纳时写 `module_id`；
   - 不能自然归类时提供新的稳定 `module_id`、`module_title` 和 `triggers`；
   - 不要为了与旧体系一致而改变新观点；冲突结论分别输出。
4. 将 JSON 写入临时文件并运行 `scripts/game-kb accept-job <job-id> <result.json>`。
5. 引文验证失败时修正当前结果，不要跳过或伪造引用。

每次 `accept-job` 只增补相关模块，不重写整个旧模块；路由、模块目录由模块 YAML 元数据自动生成。

## 内容保持规则

- 不进行立场、安全、风险或偏激程度筛选，不因观点刺耳而删除、改写或降权。
- 每个来源先独立保存；原件写入 `raw/sha256/` 后只读，提取和蒸馏不得覆盖原件。
- 完全重复文件复用同一原件，但保留所有文件名和原路径 alias。
- 相互冲突的观点并列写明来源；不要替用户裁决。
- `direct/reported/inference` 只用于回溯证据，不是价值判断。
- 不确定的抽取块进入 `unparsed/`；解析失败不得静默丢弃。

## 发布边界

普通查询、普通 `update`、`build-full` 和 `verify` 不进行 GitHub、Drive、Git 提交或安装目录写入。
用户明确要求“更新并发布”后，CLI 才执行完整验证、版本递增、打包、提交推送、GitHub Release、Drive 校验和本机双安装同步。任一步失败都报告 `partial` 并保留本地发布状态供重试。
