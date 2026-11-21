# 安装来源与目录

此技能以桌面 `company-profile-complete-handoff-3133.zip` 为来源。安装时核对了整包 SHA-256 `e941db83f1e8e998043b730a42d43d6a36dff00dafad9a58f7c0eac738a0858c`，并逐项核对清单中 69 个文件的字节数和哈希。清单自身是第 70 个文件。

| 源包位置 | 安装位置 |
|---|---|
| `company-profile/assets/`、`company-profile/references/`、`company-profile/scripts/` | 技能根下对应的 `assets/`、`references/`、`scripts/` |
| `production/` | 技能根下的 `production/`，保留原版本及模块间的依赖关系 |
| 原技能入口和界面描述 | `assets/handoff/entrypoints/company-profile-skill.md.txt` 与 `company-profile-openai.yaml.txt` |
| 根清单、交接说明、参考状态、静态依赖、`cloud-original/` | `assets/handoff/` 下的同名文件和目录 |
| `verify_handoff.py` | `assets/handoff/verify_handoff.py.txt`，核验原 ZIP 解压目录用的自检脚本原文 |

Master 版本新增了本地源文件读取、批次准备和 Library 写入入口。移植时对两个随包脚本做了 Windows 适配，分别是目录扫描器 `scripts/pipeline_directory.py` 的文件打开与身份核对，以及 `scripts/test_snapshot.py` 的 SQLite 连接释放。原 ZIP 的清单描述分发时的字节，`production/` 内的 12 个固定模块均与清单匹配。

日常操作从 [library 技能入口](../SKILL.md) 开始。交接资料中的 `STATE-REDACTED.json` 记录原打包时点的状态，`production/` 中历史编排程序带原机的路径与目标绑定。通用演练按需参考 `assets/starter/` 和 `assets/source-fixtures/`。本次指定的本地资料和 Research Hub 正式 Library 的使用方法以当前技能入口为准。
