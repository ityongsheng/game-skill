# game-skill v2.0 · 搭讪学 + 两性博弈 完整技能体系

> 基于 Mikey（搭讪玩家 TV）76 个视频 + FanGame（凡公子 / Tankman）78 个视频的系统提炼  
> 共 154 个视频、约 134 万字原始转录 | 模块化版本 v2.0 | 2026-06-23

## 是什么

这是一个 **AI 技能知识库**，将 Mikey 和 FanGame 两位搭讪学导师的公开视频内容系统提炼为结构化、可路由加载的模块。适用于 Claude Code 的 Skill 系统，也可作为独立知识库阅读。

涵盖：搭讪实战、约会全流程、网聊技巧、心态与内核、框架控制、女本位批判、17 种女生类型处理策略、常见错误大全。

## 目录结构

```
skill.yaml                  # 主路由表：触发关键词 / 意图匹配 / 技术速查
├── modules/
│   ├── a_mikey_system.md   # Mikey 搭讪学体系 (A1-A7)
│   ├── b_tankman_system.md # Tankman 体系 (B1-B5)
│   ├── c_girl_types.md     # 17 种女生类型处理策略（含双导师合成）
│   ├── d_error_ledger.md   # 常见错误大全 (5 大分类 + 阶段自查)
│   └── f_golden_quotes.md  # 原文金句录 (含话题/场景标签索引)
├── decision_trees/
│   ├── approach_flow.md    # 搭讪场景决策树
│   └── date_flow.md        # 约会转场决策树
├── templates/
│   └── scripts.json        # 话术模板库（开场/邀约/ASD 破解/微信回复）
└── index/
    └── cross_mapping.csv   # Mikey vs Tankman 双导师交叉对照
```

## 核心概念

| 体系 | 导师 | 核心方法论 |
|------|------|-----------|
| A 模块 | Mikey（搭讪玩家） | 自然流搭讪、隐形支配、状态流、NPC 理论、筛选心态 |
| B 模块 | FanGame（凡公子 / Tankman） | 两性博弈、价值匹配、三线全修、认知破局、做局法 |

## 使用方式

### 作为 Claude Code Skill

将本仓库放到 `~/.claude/skills/game-skill/` 目录下，Claude Code 会自动识别 `SKILL.md` 的前置元数据并在匹配触发词时激活。

**触发词举例**：搭讪、约会、框架、怎么聊、她不回消息、女生类型、御姐、ASD、转场、隐形支配……

### 作为独立知识库

直接从 `modules/` 目录按主题阅读：
- 想学搭讪 → `a_mikey_system.md` A1
- 想学约会 → `a_mikey_system.md` A2 +  `decision_trees/date_flow.md`
- 想学聊天 → `a_mikey_system.md` A3
- 想学心态 → `a_mikey_system.md` A4
- 想了解某类女生 → `c_girl_types.md`
- 自查常见错误 → `d_error_ledger.md`
- Mikey vs Tankman 对比 → `index/cross_mapping.csv`

## 第三方引用声明

> 本技能内容基于 Mikey（搭讪玩家 TV）和 FanGame（凡公子 / Tankman）的公开视频内容提炼。  
> 所有知识产权归原作者所有。本仓库仅作为 AI 辅助学习工具，不构成行为指导。  
> 内容仅供成年用户参考，请注意现实社交中的法律和道德边界。

## License

本仓库为知识提炼性质的学习材料，原始内容版权归原作者所有。代码和结构部分采用 MIT License。
