# workspace —— 任务 skill 锻造工作空间

每接到一个大型综合任务，按 `skills/task-skill-forge` 元 skill 的五阶段流程，在此建一个 `<task-slug>/` 目录：

```
workspace/<task-slug>/
├── 01_调研报告.md      # 阶段一
├── 02_交付件定义.md    # 阶段二（阶段三回补中间交付件）
├── 03_流程设计.md      # 阶段三
├── 04_分工方案.md      # 阶段四
├── 交付件规范/         # 阶段五：每个交付件的格式与内容规范
├── skills/             # 阶段五：每个 agent 一个独立子目录 + SKILL.md
├── flow.md             # 阶段五：流程编排（配合顺序/触发条件/质量门/异常回退）
├── 专家名册.md         # 阶段六：agent→专家映射、SSH、上岗验证记录
└── 交互留档/           # 阶段六：manager↔专家交互（一次一文件）+ 交付件流转.md
```

规则：
- 一个任务一个目录，命名用 kebab-case 任务短名。
- 六阶段顺序执行，质量门不过不进下一阶段。
- 所有结论落盘，不留口头约定。
- 执行期留档是规定动作：派发当时写、验收当时写，不事后补记。
- 缺专家时按 `skills/task-skill-forge/references/expert_onboarding.md` 在本机建号上岗。
