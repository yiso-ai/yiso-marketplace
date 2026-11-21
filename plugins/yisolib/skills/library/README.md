# Library

`Library` 是一个本地投研归档 Skill。用户给出文件或文件夹后，它负责：

> **兼容性声明**：本 Skill 按 Claude Code 插件标准打包，入口是 `plugins/yisolib/skills/library/SKILL.md`。非 Claude Agent 使用或安装时，需按自身插件/技能标准转换目录、清单和调用方式。

- 读取原件并记录内容哈希；
- 把材料按研究事项拆成独立 Memo；
- 为每篇 Memo 确定主公司和主主题，并绑定正文证据；
- 记录来源主体、材料类型、渠道、机构、作者和题名；
- 对持续更新的 Word 识别旧稿，只归档新增和修订；
- 为新公司确定申万 2021 一级或二级行业；
- 生成批次，审阅后写入 Library，并按回执报告结果。

## 调用

```text
用 $library 试读我指定的资料目录，先给我看拟归档清单，不要写库。
```

正式归档：

```text
用 $library 归档这批资料，并汇报新增、覆盖、跳过和待处理项。
```

## 目录

- `SKILL.md`：技能入口和工作流；
- `references/`：本地流程、编辑规则、数据库、图片、行业、主题与验收；
- `scripts/`：扫描、批次、写入、校验和回归；
- `assets/starter/`：合成 SQLite 与四栏阅读模板；
- `production/`：本地写入所依赖的固定运行模块。
- `openai.yaml`：OpenAI/Codex 技能界面元数据，Claude Code 不加载。

## 合成演示

需要 Python 3.10+（本次验证使用 Python 3.12）。

```bash
python assets/starter/starter.py init --db /tmp/library-demo.sqlite
python assets/starter/starter.py import --db /tmp/library-demo.sqlite --input assets/starter/fixture.json
python assets/starter/starter.py serve --db /tmp/library-demo.sqlite --port 8765
```

打开 <http://127.0.0.1:8765>。模板只使用合成数据，并仅绑定本机地址。

## 边界

安装技能不等于授权读取资料、邮箱或数据库。“试读”“预览”“查重”只生成审阅内容；明确说“归档”后才写入。真实环境接入前，应确认来源权限、目标数据库、图片备份、回执和恢复方式。
