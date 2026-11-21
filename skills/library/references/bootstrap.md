# 安装、新建与部署

## 把技能交给另一位用户

在当前产品的技能目录使用官方分享入口，由技能所有者完成分享；接收者打开实际分享链接并安装。不要把个人技能仓库的认证Git URL当共享链接，也不猜造分享URL。若产品支持上传技能包，可把本技能完整目录打成ZIP（含SKILL.md、references、scripts、assets），由接收者按其当前技能安装流程安装。Codex CLI 的本地技能位置依该产品当前说明，不能声称本地拷贝自动同步至ChatGPT。

安装后接收者说：“用 company-profile，根据我提供的研究资料搭建私有数据库和前端。先用合成数据演示，不连接邮箱、不回填历史、不定时运行。” 技能是可复用指导和起点，不会自动获得原用户资源。

## 新实例的最小输入

让接收者确认：自己的授权来源/账户或上传资料；一次性样本还是持续归档；是否已有档案；部署目的地及私有受众；语言/时区；行业分类来源与版本；日期/来源未知的处理。无须索取原实例ID或凭证。不要在聊天收密码；需要连接时使用官方插件/安全登录流程。

## 本地合成演示

先读 `assets/starter/` 中脚本的 `--help` 与 `scripts/test_starter.py`。在技能根目录运行（选择不存在的独立数据库文件）：

```sh
python assets/starter/starter.py init --db /tmp/company-demo.sqlite
python assets/starter/starter.py import --db /tmp/company-demo.sqlite --input assets/starter/fixture.json
python assets/starter/starter.py serve --db /tmp/company-demo.sqlite --port 8765
```

打开 `http://127.0.0.1:8765`。测试：`python scripts/test_starter.py`，以及 `node --check assets/starter/app.js`。模板仅绑定127.0.0.1、无HTTP写入口，SQLite文件保留本地；这是单机验证，不是公网认证方案。按实际CLI的帮助创建数据库、导入synthetic fixture并启动reader。不要将服务器改为0.0.0.0后处理私人资料。真实导入必须另给本人授权输入，不能把合成样本混入研究统计。

可运行模板包含公司/主题、主归属、正文/证据、四栏阅读、原日期/标题/显示作者、行业三级树/版本/分类性质、多层主题/别名/交叉关系、关联覆盖数、原生表格、分页完整性和事务导入。另附受认证Worker/D1适配，见 [worker-deployment.md](worker-deployment.md)。

本地pipeline另提供append-only来源修订/审阅/覆盖ledger、精确跨来源重复排除、游标/gap及中断恢复。仍不包含既有生产系统的完整schema、云端历史修订执行协议、自动邮箱连接器、远程分块/outbox或完整生产恢复控制面。`database.md` 和 `frontend.md` 是完整目标契约，不能把未实现项当成模板现成功能。`schema.sql` 含本地初始化seed；Worker migration已将初始化单独分离，Sites部署仍须按目标官方迁移流程转换验证。保守文字检查不是完整秘密检测器。

完整前端/数据库契约见 database.md 和 frontend.md。样本输出明确标记合成；完成一个实际样本的逐条语义检查后，才扩到该用户明确授权的更多材料。模板检查通过不代表来源正确、完整分页、持续任务或生产权限验收完成。

## 路线A：Sites托管D1，推荐同类体验

1. 读取当前 Sites skill 及其 storage、identity-and-secrets 参考；使用当前受支持的 server-backed starter，不能将Python localhost服务直接上传当Cloudflare Worker。
2. 在该用户独立源仓库使用 `assets/starter/worker/index.mjs` 适配及其build脚本，已提供服务端身份校验、只读API、独立受控导入及嵌入的阅读前端。按worker-deployment.md验证；把schema-only SQL映射为目标项目受支持的Drizzle schema/迁移/journal，不能假设任意SQL文件自动生效。不要复制旧实例的host manifest、registry或运行数据。
3. 用官方 create_site 创建新的私有Site，将**返回的**ID写入 `.openai/hosting.json`。声明逻辑 `d1` binding（通常DB），需要私有文件存储才加R2；由Sites配置实际资源。不要猜D1数据库ID或自行填云账户凭证。
4. 只在确认Sites dispatch完整覆盖入口后配置 SITES_DISPATCH_TRUSTED；通过已登录本人 `/api/identity` 取得当前Site-scoped ID后配置OWNER_USER_ID，不用账户allowlist ID猜测。IMPORT_ENABLED默认不启用，仅在授权导入窗口打开。在服务端实施平台身份校验及所有API的权限约束，包括错误路径、搜索和导出；不要仅靠隐藏按钮。公开静态bundle不包含研究、证据、来源定位、作者私人资料或令牌。采用当前官方身份API，匿名与未授权请求必须拒绝。
5. 迁移仅schema、增量追加，已应用SQL和journal不可改；seed/backfill分离，在已鉴权、受限的导入流程执行。每次D1 prepare只执行一个语句，使用batch组成受控事务；不要在HTTP读请求中建表或升级schema。先本地测试再走官方source同步、build/package、save/deploy流程。
6. 完成验收后保持owner-private；任何分享由新用户单独授权。数据直接来自D1，普通研究归档不重发前端源码。

**权属与可迁移性**：Sites托管D1由平台供应/绑定，不能承诺出现在用户自有Cloudflare控制台，也不代表用户获得Cloudflare账户管理权。应在受权后提供应用级完整导出/恢复能力，测试实际可用性；若平台无直接SQL导出接口，采用受鉴权且分页完整的应用级导出，不能声称“已经备份”。源码和数据库分别备份。

## 路线B：用户自管

如果用户要求自己的Cloudflare账户/D1、PostgreSQL或SQLite主机，明确这是单独部署：用其本人资源、身份认证、秘密管理、备份与运维；需要的账户/权限由本人授权。按目标方言适配SQL/事务/全文检索，禁止混用Sites内部D1身份。新环境先导入空schema与合成测试，再做一致性验证。自管不代表自动公开；本地server只能作为开发工具，不作为公网生产服务。

## 接入研究来源与增量流程

同一技能附带本人文本目录读取及capture/审阅/增量ledger工具，按 [source-adapters.md](source-adapters.md) 接入，先用合成多页来源测试，再用本人明确授权资料。邮箱必须由接收者自己的原生连接器执行真实读取；安装ZIP不自动登录、启动扫描或设置日程。不要把离线capture和当前浏览器身份导入接口宣传为已经接通无人值守邮件归档。

## 实际交付

交付确认过的安装入口或完整技能包、所选新实例URL与实际访问范围、数据库和前端版本、测试结果及未完成项。明确哪些是可运行演示，哪些已部署/接入真实来源。新用户无需获得原用户数据库即可搭建相同信息结构和阅读形式；不保证像素一致、继承私有研究、现成生产runner或一键复用原账户。
