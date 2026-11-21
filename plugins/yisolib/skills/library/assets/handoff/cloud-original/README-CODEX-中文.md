# Company Profile：Codex 离线交接包（云端技能部分）

此包为最新云端 skill 的完整 49 个文件，不是本机生产归档程序的替代品。2026-11-18 已更新包含关键增量图片的规则；规则与实现能力必须分开。

## 内容及依赖
- company-profile/：SKILL.md、全部 references、scripts、agents、assets，包含合成演练、SQLite 文字模板、Worker 模板和测试。
- Python 脚本使用 Python 3 标准库，没有额外 pip 依赖；本次云端测试版本以 VALIDATION.txt 为准，不保证其他版本兼容。
- Worker 模板另需 Node.js（仅在使用该模板时），不要求本机生产归档改用 Worker 或 D1。
- 原生邮箱/文件连接器、登录和日程不会随本包安装。此包不含连接器认证或 Library 传输工具；不能用手写 capture 代替实际读取与审核。

## 使用与安装
1. 先在独立目录解压；让 Codex 阅读 company-profile/SKILL.md、references/images.md 和本说明。
2. 此步骤只提供可读文件，不自动修改 Codex 设置、不自动安装或覆盖已有技能、不启动归档。
3. 使用 Codex 当前受支持的本地 skill 安装流程安装 company-profile 目录前，核对同名技能，保留现有版本；不要盲目覆盖。
4. 所有测试只指向独立临时目录。可在 company-profile 中运行 `python -m unittest discover -s scripts -p 'test_*.py'`；不要指向正式库。
5. 在原归档执行者仍活动时，不得以 Codex 启动第二写入者、重配凭据、清理锁或切换运行版本。真正接续要由单一执行者交接，现场核正式库、锁、批次回执和备份。

## 本机生产部分待安全间隙另附
- 固定生产发布六模块：archiver.py、controlled_writer.py、extensions.py、image_assets.py、new_entities.py、primary_subject.py。
- 现用文字编排、literal_entity_gate.py、navigation_bookmarks.py、mode-transition-v3.py；既有主体窄图片编排另列适用范围。
- 需实际执行者补齐原路径、全 SHA256、完整依赖、脱敏占位配置及安全运行说明。
- 图片生产库已有原图及 EMF/预览支持；云端模板未实现其完整链路，不得拿文字模板替换生产程序。
- 新图片编排仍属隔离适配草稿，未发布版本不能标成已验收生产入口。

## 排除项
不含数据库、原文、真实图片、批次票据、执行状态、锁、日志、凭据、认证缓存或用户运行配置。合成 fixtures 仅演练，不混入研究统计。GBrain 图片迁移只是方案，没有在此包实现或授权实际迁移。旧档不回补；每批备份，历史备份由用户管理，不自动删除。
