#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
短词/单值 UI 词条补齐器
Claude 升级后同一英文短词（New / Search / Done ...）会出现多个新 key，
其中部分仍是英文。本脚本按内置词典把这些短词统一译为规范中文。

只处理「整条就是该短词」的情况（精确匹配英文原文），不会误伤长句。
用法: python3 fill_short.py [--dry-run]
"""
from __future__ import annotations
import argparse, json, shutil, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_paths as P

PATCH_DIR = P.PATCH_DIR
ZH_FILE = P.ZH_SRC
EN_FILE = P.require_en()
APP_ZH = P.app_zh_file()

# 标准 UI 短词词典（英文原文 → 规范中文）
SHORT_DICT = {
    # 动作/按钮
    "New": "新建", "Create": "创建", "Add": "添加", "Delete": "删除",
    "Remove": "移除", "Edit": "编辑", "Save": "保存", "Cancel": "取消",
    "Close": "关闭", "Open": "打开", "Done": "完成", "Retry": "重试",
    "Submit": "提交", "Apply": "应用", "Reset": "重置", "Back": "返回",
    "Next": "下一步", "Previous": "上一步", "Skip": "跳过", "Continue": "继续",
    "Copy": "复制", "Paste": "粘贴", "Cut": "剪切", "Clear": "清除",
    "Search": "搜索", "Filter": "筛选", "Sort": "排序", "Refresh": "刷新",
    "Share": "分享", "Update": "更新", "Upgrade": "升级",
    "Upload": "上传", "Download": "下载", "Import": "导入", "Export": "导出",
    "Send": "发送", "Stop": "停止", "Start": "开始", "Pause": "暂停",
    "Resume": "继续", "Run": "运行", "Restart": "重启", "Reload": "重新加载",
    "Connect": "连接", "Disconnect": "断开连接", "Install": "安装",
    "Uninstall": "卸载", "Enable": "启用", "Disable": "禁用",
    "Sign in": "登录", "Sign out": "退出登录", "Log in": "登录", "Log out": "退出登录",
    "Learn more": "了解更多", "View all": "查看全部", "View more": "查看更多",
    "Show more": "显示更多", "Show less": "收起", "See all": "查看全部",
    "Edit name": "编辑名称", "Rename": "重命名", "Duplicate": "复制副本",
    "Move": "移动", "Pin": "固定", "Unpin": "取消固定", "Archive": "归档",
    "Customize": "自定义", "Configure": "配置", "Manage": "管理",
    # 状态/标签
    "On": "打开", "Off": "关闭", "Enabled": "已启用", "Disabled": "已禁用",
    "Active": "活跃", "Inactive": "未激活", "Idle": "空闲", "Busy": "忙碌",
    "Pending": "待处理", "Running": "运行中", "Stopped": "已停止",
    "Completed": "已完成", "Failed": "失败", "Success": "成功",
    "Error": "错误", "Warning": "警告", "Loading": "加载中",
    "Saving": "正在保存", "Saved": "已保存", "Searching": "正在搜索",
    "Restarting": "正在重启", "Connecting": "正在连接", "Thinking": "思考中",
    # 名词/导航
    "Settings": "设置", "Preferences": "偏好设置", "Help": "帮助",
    "About": "关于", "Account": "账户", "Profile": "个人资料",
    "Projects": "项目", "Project": "项目", "Tasks": "任务", "Task": "任务",
    "Artifacts": "制品", "Artifact": "制品", "Scheduled": "已定时",
    "Sessions": "会话", "Session": "会话", "Messages": "消息", "Message": "消息",
    "Files": "文件", "File": "文件", "Folders": "文件夹", "Folder": "文件夹",
    "Members": "成员", "Member": "成员", "Teams": "团队", "Team": "团队",
    "Workspace": "工作区", "Workspaces": "工作区", "Agents": "智能体",
    "Agent": "智能体", "Model": "模型", "Models": "模型",
    "Tools": "工具", "Tool": "工具", "Skills": "技能", "Skill": "技能",
    "Plugins": "插件", "Plugin": "插件", "Connectors": "连接器",
    "Connector": "连接器", "Routine": "例程", "Routines": "例程",
    "Plan": "方案", "Plans": "方案", "Usage": "用量", "Billing": "账单",
    "Privacy": "隐私", "Security": "安全", "General": "通用",
    "Advanced": "高级", "Developer": "开发者", "Integrations": "集成",
    "Memory": "记忆", "Language": "语言", "Theme": "主题",
    "Notifications": "通知", "Shortcuts": "快捷键",
    "Yes": "是", "No": "否", "OK": "确定", "None": "无", "All": "全部",
    "More": "更多", "Less": "更少", "Custom": "自定义", "Default": "默认",
    "Today": "今天", "Yesterday": "昨天", "This week": "本周",
    "This month": "本月", "Last week": "上周", "Last month": "上个月",
    "Day": "天", "Week": "周", "Month": "月", "Year": "年",
    "Hour": "小时", "Minute": "分钟", "Second": "秒",
    "Light": "浅色", "Dark": "深色", "System": "跟随系统",
    "Small": "小", "Medium": "中", "Large": "大",
    "Low": "低", "High": "高", "Auto": "自动",
}

def norm(s: str) -> str:
    return (s or "").strip()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    zh = json.loads(ZH_FILE.read_text(encoding="utf-8"))
    en = json.loads(EN_FILE.read_text(encoding="utf-8"))

    # 建立小写索引，容忍大小写差异
    low_index = {k.lower(): v for k, v in SHORT_DICT.items()}

    hits = []
    for k, e in en.items():
        cur = zh.get(k, e)
        if norm(cur) != norm(e):
            continue                 # 已有译文
        tgt = SHORT_DICT.get(norm(e)) or low_index.get(norm(e).lower())
        if tgt:
            hits.append((k, e, tgt))

    print("=" * 66)
    print(" 短词/单值词条补齐" + ("（预演）" if args.dry_run else ""))
    print("=" * 66)
    print(f"词典条目: {len(SHORT_DICT)}")
    print(f"可补齐: {len(hits)} 条\n")
    seen = set()
    for k, e, t in hits:
        if e not in seen:
            seen.add(e)
            print(f"  {k:16} {e:<22} -> {t}")

    if args.dry_run or not hits:
        if args.dry_run:
            print(f"\n[预演] 共 {len(hits)} 条待补")
        else:
            print("\n无需补齐")
        return 0

    for k, e, t in hits:
        zh[k] = t

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(ZH_FILE, PATCH_DIR / f"zh-Hans-final.json.bak-{ts}")
    ZH_FILE.write_text(json.dumps(zh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    APP_ZH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ZH_FILE, APP_ZH)
    print(f"\n✓ 已补齐 {len(hits)} 条并同步应用")
    return 0

if __name__ == "__main__":
    sys.exit(main())