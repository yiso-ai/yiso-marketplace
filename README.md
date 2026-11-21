# Library for Claude Code

一个可安装的 Claude Code 插件示例：把 `library` Skill、插件索引和静态安装引导页放在同一个 Git 仓库里。

仓库根目录就是插件根目录，`docs/` 是给人看的安装页面。没有市场网站、没有构建步骤、没有运行时依赖。

## 这个仓库包含什么

```text
yiso-marketplace/
├── .claude-plugin/
│   ├── plugin.json                  # 插件清单
│   └── marketplace.json             # 仓库内插件索引
├── skills/
│   └── library/
│       ├── SKILL.md                 # 技能入口
│       ├── references/              # 本地流程、数据库、图片、行业和验收规则
│       ├── scripts/                 # 扫描、批次、写入、校验与回归脚本
│       ├── assets/
│       │   ├── starter/             # SQLite + 合成 fixture + 阅读界面
│       │   ├── source-fixtures/     # 通用结构样例
│       │   └── icon.svg
│       └── production/              # 本地归档依赖的固定运行模块
└── docs/
    ├── index.html                   # 安装与使用说明
    ├── style.css
    ├── app.js                       # 复制命令、仓库地址和安装提示
    ├── CNAME                        # GitHub Pages 自定义域名
    └── .nojekyll                    # 跳过 Jekyll 处理
```

## 发布安装页

`docs/` 通过 GitHub Pages 发布到 <https://yiso.asia/>。发布源为 `main` 分支的 `/docs` 目录。

DNS 在阿里云解析设置：

- `A` 记录：主机记录 `@`，值分别为 `185.199.108.153`、`185.199.109.153`、`185.199.110.153`、`185.199.111.153`
- `AAAA` 记录：主机记录 `@`，值分别为 `2606:50c0:8000::153`、`2606:50c0:8001::153`、`2606:50c0:8002::153`、`2606:50c0:8003::153`
- `CNAME` 记录：主机记录 `www`，值为 `yiso-ai.github.io`

然后在 GitHub 仓库进入 `Settings → Pages`，选择 `Deploy from a branch`、分支 `main`、目录 `/docs`，并把自定义域名设为 `yiso.asia`。DNS 生效并签发证书后，启用 `Enforce HTTPS`。

## 安装

在终端执行：

```bash
claude plugin marketplace add yiso-ai/yiso-marketplace
claude plugin install yisolib@yiso-marketplace
```

安装后在 Claude Code 中执行 `/reload-plugins`，或重新启动会话。

`.claude-plugin/marketplace.json` 只是仓库内的插件索引，不是插件市场网站，也不需要账号或后端服务。

## 调用

```text
/yisolib:library 试读我指定的资料目录，先给我看拟归档清单，不要写库。
```

正式归档：

```text
/yisolib:library 归档这批资料；新公司按主营业务归入申万 2021 二级，无法区分二级时归入一级。
```

Word 增量：

```text
/yisolib:library 比对这份更新的 Word，只归档已核实的新增和修订；有候选旧稿时先让我确认。
```

## 它做什么

- 读取用户明确给出的本地文件或文件夹，记录文件哈希、格式和可读性。
- 按研究事项把材料拆成独立 Memo，保留内部分析的原始段落顺序。
- 每篇 Memo 只设一个主归属，并绑定当前正文中的原文证据。
- 记录来源主体类型、材料类型、渠道、机构、作者和材料题名。
- 对持续更新的 Word 识别旧稿，只归档已核实的新增和修订。
- 为新公司按主营业务判断申万 2021 一级或二级行业。
- 生成批次，先审阅再写入；写入后按回执报告新增、覆盖、跳过和待处理项。
- 只把选中的关键图片和必要预览作为额外文件保存。

## 本地合成演示

需要 Python 3.10+（本次验证使用 Python 3.12）。在仓库的 `skills/library` 目录下运行：

```bash
python assets/starter/starter.py init --db /tmp/library-demo.sqlite
python assets/starter/starter.py import --db /tmp/library-demo.sqlite --input assets/starter/fixture.json
python assets/starter/starter.py serve --db /tmp/library-demo.sqlite --port 8765
```

然后打开 <http://127.0.0.1:8765>。

这个模板只绑定本机 `127.0.0.1`，使用合成数据，不是公网认证方案，也不是现有生产 runner 的替身。

## 安全边界

- 安装插件不等于授权读取任何文件、邮箱或数据库。
- 只读取用户在本次任务中明确给出的路径；材料里提到的其他位置不会自动扩大范围。
- “试读”“预览”“查重”只生成审阅内容；明确说“归档”后才写入。
- 关键图片可以保存，但不把 OCR 结果冒充原生文字，也不从截图推导新正文。
- 数据库、出处记录和图片默认留在本地或用户配置的私有环境。
- 真实环境接入前，先核验来源权限、目标数据库、图片备份、回执和恢复方式。

## 从旧名迁移

插件的旧标识是 `yiso@yiso-plugin`，现为 **`yisolib@yiso-marketplace`**（市场名 `yiso-marketplace`，插件名 `yisolib`）。已经装过旧版的话：

```bash
claude plugin uninstall yiso
claude plugin marketplace remove yiso-plugin
claude plugin marketplace add yiso-ai/yiso-marketplace
claude plugin install yisolib@yiso-marketplace
```

然后执行 `/reload-plugins`。命令入口也从 `/yiso:library` 变为 **`/yisolib:library`**。

## 更新与卸载

```bash
claude plugin marketplace update yiso-marketplace
claude plugin update yisolib
```

然后执行 `/reload-plugins`。

```bash
claude plugin uninstall yisolib
```

## 手动安装

如果暂时不能使用 `claude plugin`，可以下载仓库，只复制 `skills/library`：

```bash
tmp="$(mktemp -d)"
curl -fsSL https://github.com/yiso-ai/yiso-marketplace/archive/refs/heads/main.tar.gz \
  | tar -xz -C "$tmp" --strip-components=1
mkdir -p ~/.claude/skills
cp -R "$tmp/skills/library" ~/.claude/skills/
```

手动安装适合首次使用；此方式下直接调用 `/library`。后续更新和卸载建议使用插件命令。

## 页面预览

`docs/` 是纯静态页面，可以直接打开，或在仓库根目录运行：

```bash
python3 -m http.server 4173 --directory docs
```

然后访问 <http://127.0.0.1:4173>。页面也支持用 `?repo=owner/repo` 覆盖 GitHub 仓库地址。
