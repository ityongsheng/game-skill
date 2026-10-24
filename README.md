# game-skill v4

供 Codex 和 Claude 共用的 Game 理论知识库 Skill：既能按模块与原始证据回答问题，也能把视频、音频、PDF、EPUB、DOCX、图片和文本增量加入本地知识库。

本仓库是控制层；默认数据层位于 `/Users/liyongsheng/Documents/Game知识库`。新来源独立保存、按哈希去重，矛盾观点并列，无法归类时新建模块。系统不对材料做立场或偏激程度筛选。

## 快速开始

```bash
scripts/game-kb doctor
scripts/game-kb init
scripts/game-kb import-full "/path/to/Game蒸馏FULL.md"
scripts/game-kb update "/path/to/new-material"
scripts/game-kb search "Mikey 一致性" --top-k 8
scripts/game-kb next-job
scripts/game-kb verify --full
```

安装和 Agent 使用协议见 [SKILL.md](SKILL.md)，完整命令与数据格式见 [KB_REFERENCE.md](KB_REFERENCE.md)。

## 发布边界

普通查询和更新不会自动推送。只有明确执行 `scripts/game-kb publish` 或 `scripts/game-kb update <path> --publish` 才会发布 GitHub/Drive 并同步本机安装目录。先用 `publish --dry-run` 可验证本地资产。

## 开发验证

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -v
```
