# Claude 汉化助手（GUI）

Claude Desktop 简体中文补丁的 macOS 图形界面，Flutter 实现。

## 功能

- 状态总览：Claude 版本、前端布局、汉化状态、integrity 校验、覆盖率、工作树、LLM 端点
- 一键汉化 / 完整更新 / 校验 / 环境体检 / 还原原版
- 实时输出日志（可复制），运行中可中断
- 设置：Claude.app 路径、工程目录、Python 解释器；LLM 部分选协议（OpenAI 兼容 / Anthropic）、
  填 Base URL 与 API Key、点「获取模型列表」后下拉选择模型

所有操作都等价于在工程根目录执行对应的命令行：

| 按钮 | 实际命令 |
|---|---|
| 一键汉化 | `./update.sh --quick` |
| 完整更新 | `./update.sh` |
| 校验补丁 | `python3 zh_patch.py verify` |
| 环境体检 | `python3 zh_patch.py doctor` |
| 还原原版 | `python3 zh_patch.py restore` |

## 开发与打包

```bash
flutter run -d macos     # 开发
flutter test             # 单元测试
flutter build macos      # → build/macos/Build/Products/Release/ClaudeHans.app
./tool/make_release.sh   # → dist/ClaudeHans-<版本>.zip（内置引擎 + ad-hoc 重签）
```

`tool/make_release.sh` 会调用 `tool/bundle_engine.py`，把仓库根目录的运行期文件
（`zh_patch.py`、翻译数据、翻译流水线脚本、`update.sh` 等）复制进
`ClaudeHans.app/Contents/Resources/engine`，然后重签、压包。release 用户因此不需要 clone 仓库。

引擎目录的查找顺序：

1. `CLAUDE_ZH_PROJECT`
2. 设置里上次选的目录
3. 从工作目录逐级向上、`~/claude-zh-patch` / `~/Documents/…` / `~/Desktop/…`
4. app 内置引擎 → 复制到 `~/Library/Application Support/claude-zh-patch/engine` 后使用
   （app 包内部不可写，写了会破坏代码签名；`.work/` 也在这份副本里）

复制时若检测到引擎版本变化（`ENGINE_VERSION`），旧的 `zh-Hans-final.json` 等用户数据会先备份到
`~/Library/Application Support/claude-zh-patch/backups/<时间戳>/`。

应用启动时用登录 shell 解析 Python ≥ 3.9；找不到时界面会给出 `xcode-select --install` /
`brew install python@3.13` 的可复制命令。配置保存在 `~/.config/claude-zh-patch/gui.json`。

## 说明

- runner 关闭了 App Sandbox（`macos/Runner/*.entitlements`），否则无法调用 python、
  也无法读写 `/Applications/Claude.app`。
- 不做任何写操作之前都会弹确认框；「一键汉化」会退出并重启 Claude。

## 图标

应用图标按「紫蓝渐变圆角方块 + 白色『文』字」设计（与界面左上角标记一致），
不使用 Flutter 默认图标。需要调整时改 [generate_app_icon.py](tool/generate_app_icon.py)
后执行，会直接重写 `macos/Runner/Assets.xcassets/AppIcon.appiconset/` 下的 7 个尺寸：

```bash
python3 -m pip install pillow
python3 tool/generate_app_icon.py
```
