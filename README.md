# Claude Desktop 中文汉化补丁

为 Mac 版 Claude Desktop（4.42xx 起，前端打包成 `ion-dist.asar`）添加简体中文界面，
含完整中文翻译质量工具链。

## 工作原理（新版）

Claude 4.42xx 起把前端资源从松散目录 `Resources/ion-dist/` 收进了
`Resources/ion-dist.asar`，并且这个包被**三重锁死**：

| 锁 | 说明 |
|---|---|
| 1. Electron fuse | `EnableEmbeddedAsarIntegrityValidation` = Enabled，加载 asar 时校验头部 |
| 2. Info.plist | `ElectronAsarIntegrity` 里记着 **asar 头部 JSON 的 SHA256** |
| 3. 整表摘要 | Electron 44 起在 `Electron Framework` 的 `__DATA_CONST,__asar_integrity` 段里烧了 `ElectronAsarIntegrity` **整张字典**的 SHA256，只改表里任一条目都会判定整表无效 |
| 4. 代码签名 | 改 `Resources/` / 二进制后，macOS 拒绝启动签名破损的包 |

所以「解包 → 改 → 重新打包」必须一次做齐：
**重算每文件 integrity → 重算头部 SHA256 写回 Info.plist → 重算整表摘要写回 Framework
→ 从内到外 ad-hoc 重签**。这些都由 `zh_patch.py` 封装。

补丁实际做的事：
1. 部署 `i18n/zh-Hans.json` + `i18n/dynamic/zh-Hans.json`
2. 注入语言白名单数组（让「语言」菜单出现「中文（简体）」）
3. 注入语言选择器名称映射
4. 重新打包 asar、更新 Info.plist hash、更新整表摘要、重签

## ⚠️ 升级后必须做的事

**Claude 每次升级（含小版本）都会完整覆盖 `ion-dist.asar`，汉化必然失效。**

升级后运行：

```bash
cd ~/claude-zh-patch && ./update.sh          # 完整更新（应用 + 补译新增文案）
cd ~/claude-zh-patch && ./update.sh --quick  # 快速（只应用，不调 LLM）
```

`update.sh` 会自动完成：
1. `zh_patch.py sync` — 解包当前版本 `ion-dist.asar` 到 `.work/`（首次会备份原版 asar / Info.plist）
2. `zh_patch.py stage` — 部署翻译 + 注入语言入口
3. `fill_short.py` — 短词补齐（New→新建、Search→搜索…，秒级）
4. `fix_terms.py` — 术语规范（PR/分支/运行…，秒级）
5. `propagate.py` — 同值传播（英文原文相同则复用旧译文，秒级）
6. `retranslate.py` + `retranslate_icu.py` — LLM 补译剩余新增文案（无本地模型则自动跳过）
7. `zh_patch.py pack` — 重新打包 + 更新 Info.plist hash + 更新整表摘要 + 重签
8. 重启 Claude 并打印校验结果与覆盖率

## 单独使用的工具

```bash
python3 zh_patch.py info        # 查看版本/布局/工作树/hash
python3 zh_patch.py sync        # 解包当前 asar 到 .work/
python3 zh_patch.py stage       # 只部署翻译+注入（不打包）
python3 zh_patch.py pack        # 工作树 → asar + 更新校验值 + 重签
python3 zh_patch.py apply       # sync + stage + pack
python3 zh_patch.py verify      # 校验 integrity / 头部 hash / 整表摘要 / 中文文件 / 覆盖率
python3 zh_patch.py restore     # 还原官方原版（含重签）

./apply.sh                      # = zh_patch.py apply
./restore.sh                    # = zh_patch.py restore

python3 scan_quality.py         # 扫描缺陷（术语误译/直译/漏译/占位符）
python3 fix_terms.py [--dry-run]# 修正已确认术语（PR/跑步/铬/分支机构…）
python3 fill_short.py           # 短词词典补齐
python3 propagate.py [--dry-run]# 同值传播
python3 retranslate.py --prepare|--run|--apply   # LLM 重译普通文案
python3 retranslate_icu.py      # LLM 重译 ICU 复合结构
python3 llm_endpoint.py         # 查看当前探测到的模型端点
```

> 除 `zh_patch.py` 外，其余工具都通过 `claude_paths.py` 解析路径：
> asar 布局下它们读写的是 `.work/ion-dist/i18n/`，所以**先跑 `zh_patch.py sync`**
> （`update.sh` 已自动处理）。

## 文件清单

| 文件 | 作用 |
|---|---|
| `zh_patch.py` | **补丁引擎**：asar 读写、integrity 摘要、重签、sync/stage/pack/verify/restore |
| `claude_paths.py` | 新旧布局统一路径解析（供所有工具复用） |
| `apply.sh` / `restore.sh` | `zh_patch.py apply/restore` 的入口 |
| `update.sh` | **升级后一键更新（推荐）** |
| `zh-Hans-final.json` | 主翻译源（40242 条，当前覆盖约 88%） |
| `zh-Hans-dynamic.json` | 动态文案翻译源（模型/能力说明等 50 条） |
| `llm_endpoint.py` | 本地模型端点自动探测 |
| `scan_quality.py` | 缺陷扫描（ICU 正确解析） |
| `fix_terms.py` | 术语修正器 |
| `fill_short.py` | 短词词典补齐 |
| `propagate.py` | 同值传播补译 |
| `retranslate.py` | LLM 普通文案重译 |
| `retranslate_icu.py` | ICU 结构重译 |
| `.work/` | 解包后的工作树 + 原版 asar / Info.plist 备份（还原用） |
| `backup/` | 旧版（松散目录时代）备份，已不再使用 |

## 关键技术点

| 问题 | 解法 |
|---|---|
| **asar 受 integrity 校验** | 重新打包时重算每文件的 `integrity`（整文件 SHA256 + 4MB 分块 SHA256） |
| **头部 hash 记在 Info.plist** | 打包后重算头部 JSON 的 SHA256 写回 `ElectronAsarIntegrity` |
| **Electron 44 的整表摘要** | 重算整张 `ElectronAsarIntegrity` 字典的 SHA256（按 key 排序拼接 key+algorithm+hash），写回 Framework 的 `__asar_integrity` 段 |
| **改资源后签名失效 → 无法启动** | 从内到外 ad-hoc 重签（嵌套 Mach-O / bundle 先签，外层最后），并清除 quarantine |
| **升级后 JS 文件名哈希变化** | 按内容特征（语言数组字面量 / 名称映射锚点）动态定位，不写死文件名 |
| **本地模型端口经常变** | `llm_endpoint.py` 自动扫描常见端口 + 模型候选；也可用 `CLAUDE_ZH_API` / `CLAUDE_ZH_MODEL` 指定 |
| **ICU 结构易被破坏** | `retranslate_icu.py` 深度感知解析，校验变量集合 / 分支键 / `#` 数量 / HTML 标签 |
| **中文语序导致变量顺序变化** | 校验用「集合比较」而非「顺序比较」 |
| **同名文案多个 key** | `propagate.py` 按英文原文传播 + `fill_short.py` 词典兜底 |
| **新增 key 过多** | LLM 分批重译 + 断点续传（`translate_done.json` / `icu_done.json`） |

## 注意事项

- **重签是必须的**：改 `Resources/` 或 Framework 二进制后，macOS 会拒绝启动签名破损的包。
  补丁用 ad-hoc 签名（保留原 entitlements），副作用是**应用签名身份改变**：
  - 钥匙串里 Electron safeStorage 的旧条目可能需要重新授权一次（首次启动可能提示）
  - Touch ID / passkey 登录可能不可用（`keychain-access-groups` 在 ad-hoc 签名下无效）
  - Claude 的自动更新走 Squirrel，签名变化后仍可更新（更新后重跑 `update.sh` 即可）
  - 想彻底还原：`python3 zh_patch.py restore`，或直接重装官方 Claude
- **系统菜单栏**（Apple/文件/编辑等原生菜单）跟随系统语言，不受补丁影响
- **品牌名** `Cowork`、`Code`、`Claude`、`Gateway` 等保留英文，符合官方本地化惯例
- 剩余未译条目基本是路径、URL、域名、文件格式名（如 `~/Documents/work`、`Word (.docx)`），本就不应翻译

## 当前生效文件

```
/Applications/Claude.app/Contents/Resources/ion-dist.asar               (翻译 + 白名单注入)
/Applications/Claude.app/Contents/Info.plist                           (ElectronAsarIntegrity hash)
/Applications/Claude.app/Contents/Frameworks/Electron Framework.framework
    /Versions/A/Electron Framework                                     (__asar_integrity 整表摘要)
```