# Company Profile 完整技能及固定运行组件交接
本包包含完整 49 个 cloud skill 文件、12 个字节原样固定生产模块及只读自检。不是一键导入、安装器、数据库备份或生产执行授权。

先读 company-profile/SKILL.md，再读本说明与生产依赖清单。cloud-original 保存原始云端交付说明，仅为历史来源，其 skill-only 状态不描述此合并包。

## 安全边界
程序硬编码本机路径和数据库 UUID。UUID 是非凭据的防误写目标绑定，不是可移植配置；旧子包“无UUID”说明错误，本包明确纠正。12 个模块原样保留，禁止为迁移擅改路径、UUID、哈希或门控。换机器需要独立迁移设计与验证，不能直接运行。
本包不包含真实数据库、研究正文、原始研究图片、凭据、实际批次票据、审批封印或含真实研究的测试报告。skill 内示例属于原 cloud 包合成演示，不能混入正式库。
同一时间仅一执行者；先核实际状态及双锁，保留所有旧备份。断线不重放，先查回执与恢复证明。锁不能凭超时删除。自检仅哈希和 Python 内存编译，不导入或执行生产模块，不启动归档。

## 3133 状态与图片能力限制
参考状态为 <memo-count> 篇、<receipt-count> 回执、generation <generation-id> paused，见 STATE-REDACTED.json；不是未来实时状态。
<research-topic> 2023-08-02 p33–50 已完成，绝不可重放。它使用一次性精确适配，并不证明旧 single-PNG v4 通用流程支持相同结构。
本机专用适配路径：<workspace>\临时隔离验证-历史memo-20261116\research-topic-a-exact-production-orchestration-draft-20261118\research-topic-a-exact-scheduler-draft.py。
该一次性程序依赖私有精确源、批次、语义审查、测试报告与审批，已消费，不属于可通用复用能力。本通用包有意不封装其票据、封印、真实数据依赖；不可宣称包含其完整可执行case。需要追溯只能在获授权本机只读核正式审计目录，不再次执行。

## 依赖与校验
固定生产 Python 为标准库实现，Windows 图像转换使用系统 PowerShell/.NET，EMF 依赖Windows图形能力；见 production/dependencies.json。skill 的前端/Worker另有Node、Cloudflare及环境配置要求，按其 references 执行，不能把skill部署当成本机归档启动。
STATIC-DEPENDENCIES.json 列出静态 Python imports，不表示所有动态路径或私有一次性case依赖可移植。固定12模块相互依赖及两版本NAV均保留；不要混用NAV版本。
解压后可用 Python 运行 verify_handoff.py。它只读取包内文件、校验SHA、compile()检查Python语法，不访问正式库、不执行待检模块。JS/SQL/HTML只做完整性保留，未运行其功能或部署测试。
