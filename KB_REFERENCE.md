# game-kb v4 查询、增量更新与发布协议

仓库 `/Users/liyongsheng/Code/codex/game-skill` 是唯一代码源。默认数据根目录：

```text
/Users/liyongsheng/Documents/Game知识库
```

可用 `--data-root`、`--repo-root` 或环境变量 `GAME_KB_ROOT`、`GAME_KB_REPO` 覆盖。

## 命令

```bash
scripts/game-kb doctor
scripts/game-kb init
scripts/game-kb import-full /path/to/FULL.md
scripts/game-kb update /path/to/file-or-directory
scripts/game-kb status
scripts/game-kb search "关键词" --top-k 8
scripts/game-kb read <chunk-id>
scripts/game-kb next-job --max-chars 12000
scripts/game-kb accept-job <job-id> result.json
scripts/game-kb generate-routes
scripts/game-kb verify --full
scripts/game-kb build-full
scripts/game-kb publish --dry-run
scripts/game-kb publish
scripts/game-kb update /path --publish
scripts/game-kb sync-installations
```

## 数据层

```text
Game知识库/
├── inbox/
├── raw/sha256/                 # 不可变原件；SHA-256 去重
├── sources/<source_id>/
│   ├── source.yaml             # JSON 形式的有效 YAML，含 alias 与状态
│   ├── extracted.md
│   ├── transcript.jsonl
│   ├── frames/
│   └── claims.jsonl
├── legacy/                     # 从历史 FULL 恢复的原路径树
├── unparsed/                   # 无法可靠拆分的原始块
├── index/kb.sqlite3            # SQLite FTS5
├── index/vectors/chunks.sqlite3# 本地中文字符 n-gram 向量
├── state/jobs.sqlite3
├── reports/
├── releases/
└── FULL.md
```

原件先复制到同目录临时文件，校验 SHA-256 后原子改名并设为只读。相同哈希只保留一份底层原件；所有不同路径和名字保留为 alias。新版本文件因哈希不同建立新来源。

## 格式提取

- 视频/音频：ffmpeg 生成 16kHz 单声道音频，本地 Whisper 生成带起止时间的 JSONL；视频同时按场景变化与最长 60 秒间隔抽关键帧，并尝试 OCR。
- PDF：先用 `pdftotext -layout`；文本层不足时 `pdftoppm` 逐页渲染并用 Tesseract OCR。
- EPUB：读取 container/OPF，严格按 spine 顺序提取 XHTML。
- DOCX：读取段落、表格和 `comments.xml` 批注。
- 图片：Tesseract OCR。
- Markdown/TXT/HTML/JSON/JSONL/字幕：保留或规范化原结构。

提取失败的来源进入 `failed` 终态并保留原件和错误，不会静默忽略。

## FULL 基线恢复

`import-full` 按编号块流式解析，不把 100MB 以上文件一次性载入 Agent 上下文。每个嵌入文本块核对声明字节数和 SHA-256 后恢复到 `legacy/` 并写入索引。二进制清单写成 `legacy/binary_manifest.jsonl`，状态为 `manifest_only`。未闭合、结构未知或不能可靠拆分的块原样写入 `unparsed/`，报告记录其路径。

FULL 文件本身不作为来源再次索引，以免内容重复。

## 检索和原文

```bash
scripts/game-kb search "Mikey 一致性 原文" --top-k 8
scripts/game-kb read src-abc123:000004
```

检索合并 FTS5 和本地中文字符 n-gram 向量候选。回答时优先使用模块概览；要求原文、出处或比较时读取命中的来源块，并同时说明 `source_id` 与 `locator`。

## Agent 蒸馏和恢复

`next-job` 优先返回上次已进入 `in_progress` 但尚未接受的任务，因此 Agent 中断后再次运行会返回同一个 `job_id`。输出必须包含：

```json
{
  "claims": [
    {
      "claim_type": "direct",
      "claim": "结论",
      "quote": "片段中逐字存在的引文",
      "locator": "时间戳/页码/段落",
      "module_id": "existing-or-new-slug",
      "module_title": "仅新模块需要",
      "triggers": ["仅新模块需要"]
    }
  ]
}
```

`accept-job` 在落盘前反向验证每条 `quote`。同一来源或不同来源相互矛盾的 claims 都会保留；它们不会彼此覆盖。没有 `module_id` 的结论仍保存在逐来源证据层并可检索。

## 模块增量

每个 `modules/*.md` 文件以 YAML 元数据声明稳定 ID、标题、触发词和优先级。`generate-routes` 生成：

- `skill.yaml`
- `index/module_catalog.json`

Agent 只在相关模块末尾追加带来源的增量章节。无法归入旧模块时创建新 slug 文件；不强迫新资料使用旧术语，也不对冲突做裁决。

## 验证

`verify --full` 检查：来源是否为终态、受管原件是否存在且哈希一致、claims 引文是否仍可在 chunk 中反查、FULL 是否可构建。待 Agent 蒸馏任务作为 warning 明示，不会伪装成已经蒸馏。

## 发布

只有明确运行 `publish` 或 `update --publish` 才进行外部写入。`publish --dry-run` 只生成本地安装包、`FULL.md.gz` 和 manifest。

正式发布按顺序执行验证、patch 版本递增、Git 提交、推送 main、GitHub Release、Drive 版本目录上传与回读校验、latest manifest 更新、本机 Codex/Claude 安装同步。状态写入 `releases/vX.Y.Z/publish-state.json`；失败时返回 `partial`，保留已完成步骤和资产。

默认 rclone remote 是 `gdrive:`，可用 `GAME_KB_RCLONE_REMOTE` 修改。凭据只由 gh/rclone 自己的本机配置管理，不写入仓库。
