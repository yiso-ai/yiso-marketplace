# 数据模型和归档语义

## 单一权威

已切换数据库的实例只写该实例数据库，前端实时读其投影；旧Page/Space/站点/副本是只读历史与恢复来源。不要以源码部署版本替代数据head，也不以某次已验数据版本为永久当前版本。运行前核真实authority、epoch、config版本/哈希、dataset head及既有run。

## 逻辑表组

新实例实现以下信息结构，可按目标数据库命名。模板提供可运行最小子集；下列是完整生产设计契约，不意味着模板已实现全部控制面。

- 公司：稳定ID、正式名、别名、证券代码+交易所、实体消歧、旧ID映射；不能按简称合并母子公司。
- 行业：保留申万 2021 完整目录和历史路径。新公司映射到已判定的二级或一级节点，区分官方分类与研究归类，保存有效期及审核证据。公司官网或年报的题名与引文随行业审核保存。行业分类与主题图分别维护。
- 主题：稳定ID、规范名、scope、别名/contextual alias、节点类型、状态、主父节点、交叉关系/facets、旧URL映射。
- 正文：immutable body及UTF-8 SHA256；memo指向正文，保存category、date/date_status、title、institution、lifecycle；revision和processing保留完整历史。
- 来源：namespace+record_key唯一身份、content_hash、私有locator、原始元数据、处理状态、memo_source和覆盖关系。来源定位、未批准展示的私人作者资料、读取日志不在reader API展示。
- 证据：memo_id、当前body_id、quote、Unicode offset区间、claim_kind、review_status/审核记录。quote必须与正文对应区间相等，不把“审核通过”字符串当实际阅读证据。
- 主归属：new/revised memo一个明确primary_subject，包括kind(company/theme)、subject_id、evidence_id、reason、approved；关联证据属于当前body且与语义审阅及主体assignment一致。公司主归属要求primary，普通实质涉及可substantive，mention不能据此归档。
- 关联：memo-company、memo-theme、company-theme relevance、公司关系及关系证据。证据绑定实际端点、事实/来源观点/未知，双向导航从同一事实行派生，避免两套不一致缓存。
- 原生表格：单元格、标题、单位、表头和限制，绑定memo/body/source hash及审核。只存非图片来源的原生表格，不能从图片再生成“原生表格”。
- 运行/恢复：dataset state、batch/run/chunk、幂等校验、单写者lease、failure/quarantine、来源cursor/head、扫描账本、逐项gap/recovery obligation、installed authority、审阅与版本身份。生产控制面不得由技能模板空配置替换。

## 正文及主归属

一份Memo一份canonical正文，目录和关联仅引用它。多公司报告可以拆成不同公司相关的独立内容，各自持有来源/去重身份；不能把同一整篇复制到每家公司。主归属不靠数组第一项、关键词或覆盖得分猜测。

每篇本地 Memo 关联来源记录，其 `metadata_json.attribution` 分别保存来源主体类型、材料类型、发布渠道、机构、原作者，以及 `intake_material_title`（归档自哪份材料）和 `original_material_title`（原始资料题名或场次）。两项题名均为可检索的纯文本，未知字段为空。归档自简报时可据实记录简报名、该篇对应的收录邮件主题和上游原题。出现多个有归属的口径时，正文说明各方贡献。历史 `category` 供已有读写程序使用。简报按事项成篇，输入内容指纹与稳定事项键用于防止重复提交；按出处检索或阅读正文无需原件或原文位置。

新写入必须有当前正文的明确主归属。迁移时可保留经核实旧归属：仅在显式主归属不存在时按已审旧映射兼容；显式字段无效必须拒绝/隔离，不回退猜测。改正文使旧证据及审核过期，应在同一受控批次重绑，不沿用旧hash审批。

目录公司是否激活不等于company行存在，应由实际有效主研究或已审旧档案资格确定。taxonomy_only和navigation_group不计有研究档案；navigation_group不能直接挂材料。

## 去重与覆盖

来源身份+内容哈希+公司/期间/事件联合检查，显示标题不是去重键。覆盖ledger保留exact_duplicate、fully_covered、partially_covered、覆盖/被覆盖Memo及审核来源。内部研究覆盖的卖方/公告/业绩会/新闻只补实质新增、不同观点或反证，替代前保留恢复快照。

**覆盖记录本身不等于前端已隐藏。** 当前参考查询不自动按coverage ledger抑制条目；新实现需要明确、已审的展示选择/生命周期策略并测试，不能以写了ledger就声称去重完成。不得仅按同季度/事件吞掉增量。

## 主题覆盖计分的解释

参考形式：按去重后的实质关联Memo数降序，其次覆盖比例，再稳定ID；不加未核的加权或时间衰减。百分比必须显示分子/分母和口径。参考实现分母包含active primary/substantive中非rejected项，可能有未审legacy placement，不能叫“全部已审覆盖率”。新实例可采用更严格已审口径，但必须显式定义和校验，不能暗中混用。

## SQL迁移

结构声明不等于完整数据库。必须一起版本管理SQL迁移、FK/check、trigger、view、index、FTS和元数据journal。应用后不可改历史迁移；追加变更。空库重放、旧版本升级、恢复演练均验证。图片资产、Memo/正文/来源绑定及预览映射按 [images.md](images.md) 管理；保留既有图片历史一致性，不以旧禁图规则清空或禁用。新实例需先验证适用图片结构与运行能力，不把说明更新当作 schema 已迁移。seed与业务backfill不进schema迁移。原部署的特例白名单/审批registry和历史修复脚本不能作为通用模板。
