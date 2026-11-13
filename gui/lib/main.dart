import 'dart:io';

// Flutter 3.50 把 material.dart 标记为 deprecated，指向尚未随 SDK 分发的
// material_ui 包；这里继续使用稳定接口。
// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'service.dart';

const _seed = Color(0xFF3E63DD);

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const HansApp());
}

class HansApp extends StatelessWidget {
  const HansApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Claude 汉化助手',
      debugShowCheckedModeBanner: false,
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      themeMode: ThemeMode.system,
      home: const HomePage(),
    );
  }

  static ThemeData _theme(Brightness b) {
    final scheme = ColorScheme.fromSeed(seedColor: _seed, brightness: b);
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      fontFamily: '.AppleSystemUIFont',
      scaffoldBackgroundColor:
          b == Brightness.dark ? const Color(0xFF141518) : const Color(0xFFF5F5F7),
      dividerColor: scheme.outlineVariant.withValues(alpha: 0.45),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  late GuiConfig _cfg;
  Map<String, dynamic>? _status;
  String? _statusError;
  bool _loadingStatus = false;
  bool _running = false;
  String _taskLabel = '';
  String? _python;
  final List<LogLine> _log = [];
  final TaskRunner _runner = TaskRunner();
  final ScrollController _scroll = ScrollController();

  bool get _ready => _cfg.projectDir.isNotEmpty;

  @override
  void initState() {
    super.initState();
    _cfg = ConfigStore.load();
    final found = ProjectResolver.resolve(_cfg.projectDir);
    if (found != null) _cfg.projectDir = found;
    _boot();
  }

  Future<void> _boot() async {
    _python = await PythonRuntime.resolve(preferred: _cfg.pythonPath);
    if (!mounted) return;
    setState(() {});
    if (_ready) await _refreshStatus();
  }

  @override
  void dispose() {
    _runner.cancel();
    _scroll.dispose();
    super.dispose();
  }

  // ------------------------------------------------------------ 环境

  Map<String, String> _env() {
    final env = <String, String>{'PYTHONUNBUFFERED': '1'};
    if (_cfg.appPath.isNotEmpty) env['CLAUDE_ZH_APP'] = _cfg.appPath;
    if (_cfg.llmApi.isNotEmpty) env['CLAUDE_ZH_API'] = _cfg.llmApi;
    if (_cfg.llmModel.isNotEmpty) env['CLAUDE_ZH_MODEL'] = _cfg.llmModel;
    if (_cfg.llmKey.isNotEmpty) env['CLAUDE_ZH_KEY'] = _cfg.llmKey;
    final py = _python;
    if (py != null && py.contains('/')) {
      final parent = File(py).parent.path;
      env['PATH'] = '$parent:${Platform.environment['PATH'] ?? '/usr/bin:/bin'}';
    }
    return env;
  }

  Future<String?> _ensurePython() async {
    if (_python != null) return _python;
    _python = await PythonRuntime.resolve(preferred: _cfg.pythonPath);
    return _python;
  }

  Future<void> _refreshStatus() async {
    if (!_ready || _loadingStatus) return;
    setState(() => _loadingStatus = true);
    final python = await _ensurePython();
    Map<String, dynamic>? st;
    String? err;
    if (python == null) {
      err = '未找到 Python ≥ 3.10。安装 Python 或在设置里指定解释器。';
    } else {
      final raw = await TaskRunner.capture(
        python,
        ['-u', '${_cfg.projectDir}/zh_patch.py', 'status'],
        cwd: _cfg.projectDir,
        env: _env(),
      );
      st = parseStatus(raw);
      if (st == null) err = '无法读取状态（可在日志里运行“环境体检”排查）';
    }
    if (!mounted) return;
    setState(() {
      _status = st;
      _statusError = err;
      _loadingStatus = false;
    });
  }

  // ------------------------------------------------------------ 任务

  void _onLine(LogLine line) {
    if (!mounted) return;
    setState(() => _log.add(line));
    _scrollToEnd();
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
      }
    });
  }

  Future<void> _runTask(String label, String executable, List<String> args) async {
    if (_running || !_ready) return;
    final python = await _ensurePython();
    if (python == null) {
      setState(() {
        _log
          ..clear()
          ..add(const LogLine('✗ 未找到 Python ≥ 3.10'))
          ..add(const LogLine('  安装：brew install python@3.13，或在设置里指定解释器'));
      });
      return;
    }
    setState(() {
      _running = true;
      _taskLabel = label;
      _log
        ..clear()
        ..add(LogLine('▶ $label'));
    });
    _scrollToEnd();
    var code = -1;
    try {
      code = await _runner.run(executable, args,
          cwd: _cfg.projectDir, env: _env(), onLine: _onLine);
    } catch (e) {
      _onLine(LogLine('✗ 启动失败: $e', stderr: true));
    }
    if (!mounted) return;
    setState(() {
      _running = false;
      _log.add(code == 0
          ? const LogLine('✓ 命令结束（退出码 0）')
          : LogLine('✗ 命令结束（退出码 $code）', stderr: true));
    });
    _scrollToEnd();
    await _refreshStatus();
  }

  Future<void> _pythonTask(String label, List<String> args) async {
    final python = _python ?? await _ensurePython();
    if (python == null) {
      setState(() {
        _log
          ..clear()
          ..add(const LogLine('✗ 未找到 Python ≥ 3.10'))
          ..add(const LogLine('  安装：brew install python@3.13，或在设置里指定解释器'));
      });
      return;
    }
    await _runTask(label, python, ['-u', '${_cfg.projectDir}/zh_patch.py', ...args]);
  }

  Future<void> _quickUpdate() async {
    final ok = await _confirm(
      '一键汉化',
      '将解包当前 Claude 版本 → 部署中文 → 注入语言入口 → 重新打包并 ad-hoc 重签，'
          '完成后自动重启 Claude。\n\n'
          '过程中 Claude 会被退出，未保存的对话请先处理。',
      ok: '开始',
    );
    if (!ok) return;
    await _runTask('一键汉化',
        '/bin/bash', ['${_cfg.projectDir}/update.sh', '--quick']);
  }

  Future<void> _fullUpdate() async {
    final ok = await _confirm(
      '完整更新',
      '在“一键汉化”基础上，再用本地 LLM 补译新增文案（可能耗时数分钟）。\n\n'
          '完成后会自动重启 Claude。',
      ok: '开始',
    );
    if (!ok) return;
    await _runTask('完整更新', '/bin/bash', ['${_cfg.projectDir}/update.sh']);
  }

  Future<void> _restore() async {
    final ok = await _confirm(
      '还原官方原版',
      '将用 .work/backup 里的原始 ion-dist.asar 与 Info.plist 覆盖当前安装，并重新签名。\n\n'
          '如果备份版本与当前 Claude 版本不一致，操作会被拒绝（可在终端加 --force 强制还原）。',
      ok: '还原',
      danger: true,
    );
    if (!ok) return;
    await _pythonTask('还原原版', ['restore']);
  }

  Future<void> _doctor() async {
    await _pythonTask('环境体检', ['doctor']);
  }

  // ------------------------------------------------------------ 设置

  Future<void> _openSettings() async {
    final result = await showDialog<GuiConfig>(
      context: context,
      builder: (_) => _SettingsDialog(
        config: _cfg.copy(),
        python: _python,
      ),
    );
    if (result == null) return;
    setState(() {
      _cfg = result;
      _python = null;
    });
    await ConfigStore.save(result);
    _python = await _ensurePython();
    if (!mounted) return;
    setState(() {});
    await _refreshStatus();
  }

  Future<bool> _confirm(String title, String message,
      {String ok = '继续', bool danger = false}) async {
    final scheme = Theme.of(context).colorScheme;
    final r = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(title),
        content: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: Text(message, style: const TextStyle(height: 1.55)),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(c, false), child: const Text('取消')),
          FilledButton(
            style: danger
                ? FilledButton.styleFrom(backgroundColor: scheme.error)
                : null,
            onPressed: () => Navigator.pop(c, true),
            child: Text(ok),
          ),
        ],
      ),
    );
    return r ?? false;
  }

  // ------------------------------------------------------------ build

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            _Header(
              running: _running,
              loading: _loadingStatus,
              taskLabel: _taskLabel,
              status: _status,
              onRefresh: _refreshStatus,
              onSettings: _openSettings,
              onCancel: _running ? _runner.cancel : null,
            ),
            Divider(height: 1, color: scheme.outlineVariant.withValues(alpha: .5)),
            Expanded(
              child: !_ready
                  ? _EmptyProject(onPick: _openSettings)
                  : LayoutBuilder(builder: (context, cons) {
                      final wide = cons.maxWidth >= 900;
                      final statusPane = _StatusPane(
                        status: _status,
                        error: _statusError,
                        loading: _loadingStatus,
                        projectDir: _cfg.projectDir,
                        python: _python,
                      );
                      final workPane = _WorkPane(
                        running: _running,
                        status: _status,
                        log: _log,
                        scroll: _scroll,
                        onQuick: _quickUpdate,
                        onFull: _fullUpdate,
                        onVerify: () => _pythonTask('校验补丁', ['verify']),
                        onDoctor: _doctor,
                        onRestore: _restore,
                        onClear: () => setState(_log.clear),
                      );
                      if (wide) {
                        return Row(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            SizedBox(width: 328, child: statusPane),
                            VerticalDivider(
                                width: 1,
                                color:
                                    scheme.outlineVariant.withValues(alpha: .5)),
                            Expanded(child: workPane),
                          ],
                        );
                      }
                      return Column(
                        children: [
                          SizedBox(height: 300, child: statusPane),
                          Expanded(child: workPane),
                        ],
                      );
                    }),
            ),
          ],
        ),
      ),
    );
  }
}

// ------------------------------------------------------------------ 顶部栏

class _Header extends StatelessWidget {
  final bool running;
  final bool loading;
  final String taskLabel;
  final Map<String, dynamic>? status;
  final VoidCallback onRefresh;
  final VoidCallback onSettings;
  final VoidCallback? onCancel;

  const _Header({
    required this.running,
    required this.loading,
    required this.taskLabel,
    required this.status,
    required this.onRefresh,
    required this.onSettings,
    required this.onCancel,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final applied = (status?['patch'] as Map?)?['applied'] == true;
    final exists = status?['app_exists'] == true;

    final (String pill, Color color) = running
        ? (taskLabel, scheme.primary)
        : !exists
            ? ('未找到 Claude', scheme.error)
            : applied
                ? ('已汉化', const Color(0xFF2E9E5B))
                : ('未汉化', scheme.outline);

    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 14, 12, 14),
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0xFF4F73E8), Color(0xFF7B5CF0)],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
              borderRadius: BorderRadius.circular(11),
            ),
            alignment: Alignment.center,
            child: const Text('文',
                style: TextStyle(
                    color: Colors.white,
                    fontSize: 20,
                    fontWeight: FontWeight.w600)),
          ),
          const SizedBox(width: 12),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Claude 汉化助手',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
              Text('Claude Desktop 简体中文补丁',
                  style: TextStyle(
                      fontSize: 12,
                      color: scheme.onSurface.withValues(alpha: .55))),
            ],
          ),
          const SizedBox(width: 16),
          _Pill(
            text: pill,
            color: color,
            leading: running
                ? const SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : null,
          ),
          const Spacer(),
          IconButton(
            tooltip: '刷新状态',
            onPressed: loading || running ? null : onRefresh,
            icon: loading
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.refresh_rounded),
          ),
          if (onCancel != null)
            TextButton.icon(
              onPressed: onCancel,
              icon: const Icon(Icons.stop_circle_outlined, size: 18),
              label: const Text('停止'),
            ),
          IconButton(
            tooltip: '设置',
            onPressed: running ? null : onSettings,
            icon: const Icon(Icons.tune_rounded),
          ),
        ],
      ),
    );
  }
}

class _Pill extends StatelessWidget {
  final String text;
  final Color color;
  final Widget? leading;

  const _Pill({required this.text, required this.color, this.leading});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .12),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withValues(alpha: .35)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (leading != null) ...[
            leading!,
            const SizedBox(width: 6),
          ] else ...[
            Container(
              width: 7,
              height: 7,
              decoration: BoxDecoration(color: color, shape: BoxShape.circle),
            ),
            const SizedBox(width: 6),
          ],
          Text(text,
              style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w500,
                  color: color)),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------ 左栏

class _StatusPane extends StatelessWidget {
  final Map<String, dynamic>? status;
  final String? error;
  final bool loading;
  final String projectDir;
  final String? python;

  const _StatusPane({
    required this.status,
    required this.error,
    required this.loading,
    required this.projectDir,
    required this.python,
  });

  String _v(String key) {
    final v = status?[key];
    if (v == null) return '—';
    return v.toString();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final patch = ((status?['patch'] as Map?) ?? const {})
        .cast<String, dynamic>();
    final coverage = ((status?['coverage'] as Map?) ?? const {})
        .cast<String, dynamic>();
    final llm = ((status?['llm'] as Map?) ?? const {}).cast<String, dynamic>();

    final percent = (coverage['percent'] as num?)?.toDouble() ?? 0;
    final total = coverage['total'] ?? 0;
    final translated = coverage['translated'] ?? 0;
    final applied = patch['applied'] == true;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _Card(
          title: 'Claude Desktop',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _kv(context, '版本', _v('app_version')),
              _kv(context, '前端布局', _v('layout')),
              _kv(context, '应用大小', _fmtMb(status?['asar_size'] as num?)),
              const SizedBox(height: 6),
              SelectableText(_v('app'),
                  maxLines: 2,
                  style: TextStyle(
                      fontSize: 11,
                      height: 1.4,
                      color: scheme.onSurface.withValues(alpha: .55))),
            ],
          ),
        ),
        const SizedBox(height: 12),
        _Card(
          title: '补丁状态',
          trailing: Text(applied ? '已汉化' : '未汉化',
              style: TextStyle(
                  fontWeight: FontWeight.w600,
                  color: applied ? const Color(0xFF2E9E5B) : scheme.outline)),
          child: Column(
            children: [
              _check(context, '中文文案', patch['zh_deployed'] == true ? true : null),
              _check(context, '动态文案', patch['dynamic_zh_deployed'] == true ? true : null),
              _check(context, 'Info.plist 头部 hash',
                  patch['header_hash_ok'] as bool?),
              _check(context, 'Electron 整表摘要',
                  patch['integrity_digest_ok'] as bool?),
            ],
          ),
        ),
        const SizedBox(height: 12),
        _Card(
          title: '翻译覆盖率',
          trailing: Text('${percent.toStringAsFixed(1)}%',
              style: TextStyle(
                  fontWeight: FontWeight.w600, color: scheme.primary)),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: LinearProgressIndicator(
                  value: (percent / 100).clamp(0.0, 1.0),
                  minHeight: 7,
                  backgroundColor: scheme.surfaceContainerHighest,
                ),
              ),
              const SizedBox(height: 8),
              Text('已译 $translated / 共 $total 条',
                  style: TextStyle(
                      fontSize: 11.5,
                      color: scheme.onSurface.withValues(alpha: .6))),
            ],
          ),
        ),
        const SizedBox(height: 12),
        _Card(
          title: '工具链',
          child: Column(
            children: [
              _check(context, '工作树已解包', status?['work_tree'] == true),
              _check(context, '本地模型可用', llm['available'] == true,
                  extra: llm['available'] == true
                      ? (llm['model'] ?? '').toString()
                      : null),
              const SizedBox(height: 6),
              Align(
                alignment: Alignment.centerLeft,
                child: SelectableText('Python: ${python ?? '未找到'}',
                    style: TextStyle(
                        fontSize: 11,
                        color: scheme.onSurface.withValues(alpha: .55))),
              ),
              Align(
                alignment: Alignment.centerLeft,
                child: SelectableText('工程: $projectDir',
                    maxLines: 2,
                    style: TextStyle(
                        fontSize: 11,
                        color: scheme.onSurface.withValues(alpha: .55))),
              ),
            ],
          ),
        ),
        if (error != null) ...[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: scheme.errorContainer.withValues(alpha: .5),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(error!,
                style: TextStyle(
                    fontSize: 12, color: scheme.onErrorContainer)),
          ),
        ],
      ],
    );
  }

  Widget _kv(BuildContext context, String k, String v) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Text(k,
              style: TextStyle(
                  fontSize: 12.5,
                  color: scheme.onSurface.withValues(alpha: .6))),
          const Spacer(),
          Text(v,
              style: const TextStyle(
                  fontSize: 12.5, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  Widget _check(BuildContext context, String label, bool? ok,
      {String? extra}) {
    final scheme = Theme.of(context).colorScheme;
    final dark = scheme.brightness == Brightness.dark;
    final icon = ok == null
        ? Icons.remove
        : ok
            ? Icons.check_circle_rounded
            : Icons.error_rounded;
    final color = ok == null
        ? scheme.outline
        : ok
            ? (dark ? const Color(0xFF4ADE80) : const Color(0xFF137333))
            : scheme.error;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Icon(icon, size: 15, color: color),
          const SizedBox(width: 8),
          Expanded(
            child: Text(label,
                style: const TextStyle(fontSize: 12.5),
                overflow: TextOverflow.ellipsis),
          ),
          if (extra != null)
            Flexible(
              child: Text(extra,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                      fontSize: 11,
                      color: scheme.onSurface.withValues(alpha: .55))),
            ),
        ],
      ),
    );
  }
}

String _fmtMb(num? bytes) {
  if (bytes == null) return '—';
  return '${(bytes / 1024 / 1024).toStringAsFixed(0)} MB';
}

class _Card extends StatelessWidget {
  final String title;
  final Widget child;
  final Widget? trailing;

  const _Card({required this.title, required this.child, this.trailing});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
      decoration: BoxDecoration(
        color: scheme.surface.withValues(alpha: 0.75),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: scheme.outlineVariant.withValues(alpha: .55)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(title,
                  style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      letterSpacing: .2,
                      color: scheme.onSurface.withValues(alpha: .7))),
              const Spacer(),
              ?trailing,
            ],
          ),
          const SizedBox(height: 8),
          child,
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------ 右栏

class _WorkPane extends StatelessWidget {
  final bool running;
  final Map<String, dynamic>? status;
  final List<LogLine> log;
  final ScrollController scroll;
  final VoidCallback onQuick;
  final VoidCallback onFull;
  final VoidCallback onVerify;
  final VoidCallback onDoctor;
  final VoidCallback onRestore;
  final VoidCallback onClear;

  const _WorkPane({
    required this.running,
    required this.status,
    required this.log,
    required this.scroll,
    required this.onQuick,
    required this.onFull,
    required this.onVerify,
    required this.onDoctor,
    required this.onRestore,
    required this.onClear,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('操作',
              style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
          const SizedBox(height: 10),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _ActionCard(
                icon: Icons.bolt_rounded,
                title: '一键汉化',
                subtitle: '解包 → 部署 → 重签 → 重启',
                accent: true,
                onTap: running ? null : onQuick,
              ),
              _ActionCard(
                icon: Icons.auto_awesome_rounded,
                title: '完整更新',
                subtitle: '含 LLM 补译新增文案',
                onTap: running ? null : onFull,
              ),
              _ActionCard(
                icon: Icons.verified_user_rounded,
                title: '校验补丁',
                subtitle: 'integrity / 注入 / 覆盖率',
                onTap: running ? null : onVerify,
              ),
              _ActionCard(
                icon: Icons.health_and_safety_rounded,
                title: '环境体检',
                subtitle: '路径 / 权限 / Python / 模型',
                onTap: running ? null : onDoctor,
              ),
              _ActionCard(
                icon: Icons.restart_alt_rounded,
                title: '还原原版',
                subtitle: '恢复官方 asar 与签名',
                danger: true,
                onTap: running ? null : onRestore,
              ),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              const Text('输出',
                  style:
                      TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
              const SizedBox(width: 8),
              if (running)
                Text('运行中…',
                    style: TextStyle(
                        fontSize: 11.5, color: scheme.primary)),
              const Spacer(),
              TextButton.icon(
                onPressed: log.isEmpty ? null : onClear,
                icon: const Icon(Icons.delete_sweep_outlined, size: 16),
                label: const Text('清空'),
              ),
              TextButton.icon(
                onPressed: log.isEmpty
                    ? null
                    : () => Clipboard.setData(ClipboardData(
                          text: log.map((e) => e.text).join('\n'),
                        )),
                icon: const Icon(Icons.copy_all_outlined, size: 16),
                label: const Text('复制'),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Expanded(child: _LogView(log: log, scroll: scroll)),
        ],
      ),
    );
  }
}

class _ActionCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final bool accent;
  final bool danger;
  final VoidCallback? onTap;

  const _ActionCard({
    required this.icon,
    required this.title,
    required this.subtitle,
    this.accent = false,
    this.danger = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final tint = danger
        ? scheme.error
        : accent
            ? scheme.primary
            : scheme.onSurface.withValues(alpha: .7);
    final enabled = onTap != null;

    return Opacity(
      opacity: enabled ? 1 : .45,
      child: Material(
        color: accent
            ? scheme.primary.withValues(alpha: .09)
            : scheme.surface.withValues(alpha: .75),
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(14),
          child: Container(
            width: 196,
            height: 96,
            padding: const EdgeInsets.fromLTRB(14, 13, 14, 12),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                  color: (accent ? scheme.primary : scheme.outlineVariant)
                      .withValues(alpha: accent ? .35 : .55)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(icon, size: 20, color: tint),
                const Spacer(),
                Text(title,
                    style: const TextStyle(
                        fontSize: 13, fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(subtitle,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                        fontSize: 11,
                        color: scheme.onSurface.withValues(alpha: .55))),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _LogView extends StatelessWidget {
  final List<LogLine> log;
  final ScrollController scroll;

  const _LogView({required this.log, required this.scroll});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest.withValues(alpha: .42),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: scheme.outlineVariant.withValues(alpha: .5)),
      ),
      child: log.isEmpty
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.terminal_rounded,
                      size: 30,
                      color: scheme.onSurface.withValues(alpha: .25)),
                  const SizedBox(height: 8),
                  Text('执行操作后，输出会显示在这里',
                      style: TextStyle(
                          fontSize: 12,
                          color: scheme.onSurface.withValues(alpha: .4))),
                ],
              ),
            )
          : SelectionArea(
              child: ListView.builder(
                controller: scroll,
                padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
                itemCount: log.length,
                itemBuilder: (context, i) {
                  final line = log[i];
                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: 1.5),
                    child: Text(
                      line.text.isEmpty ? ' ' : line.text,
                      style: TextStyle(
                        fontFamily: 'Menlo',
                        fontSize: 11.5,
                        height: 1.45,
                        color: _lineColor(line, scheme),
                      ),
                    ),
                  );
                },
              ),
            ),
    );
  }

  Color _lineColor(LogLine l, ColorScheme s) {
    final dark = s.brightness == Brightness.dark;
    final t = l.text.trimLeft();
    if (t.startsWith('✓')) {
      return dark ? const Color(0xFF4ADE80) : const Color(0xFF137333);
    }
    if (t.startsWith('✗')) {
      return dark ? const Color(0xFFFF7B72) : const Color(0xFFB3261E);
    }
    if (t.startsWith('⚠')) {
      return dark ? const Color(0xFFFBBF24) : const Color(0xFF9A6700);
    }
    if (t.startsWith('▶')) return s.primary;
    if (t.startsWith('#') || t.startsWith('===')) {
      return s.onSurface.withValues(alpha: .45);
    }
    if (l.stderr) return s.error;
    return s.onSurface.withValues(alpha: .82);
  }
}

class _EmptyProject extends StatelessWidget {
  final VoidCallback onPick;

  const _EmptyProject({required this.onPick});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.folder_off_outlined,
              size: 44, color: scheme.onSurface.withValues(alpha: .3)),
          const SizedBox(height: 14),
          const Text('没有找到汉化工程目录',
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Text('请选择包含 zh_patch.py 的目录',
              style: TextStyle(
                  fontSize: 12.5,
                  color: scheme.onSurface.withValues(alpha: .55))),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: onPick,
            icon: const Icon(Icons.folder_open_rounded, size: 18),
            label: const Text('选择目录'),
          ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------ 设置

class _SettingsDialog extends StatefulWidget {
  final GuiConfig config;
  final String? python;

  const _SettingsDialog({required this.config, required this.python});

  @override
  State<_SettingsDialog> createState() => _SettingsDialogState();
}

class _SettingsDialogState extends State<_SettingsDialog> {
  late final TextEditingController _project;
  late final TextEditingController _app;
  late final TextEditingController _python;
  late final TextEditingController _api;
  late final TextEditingController _model;
  late final TextEditingController _key;
  String? _error;

  @override
  void initState() {
    super.initState();
    _project = TextEditingController(text: widget.config.projectDir);
    _app = TextEditingController(text: widget.config.appPath);
    _python = TextEditingController(text: widget.config.pythonPath);
    _api = TextEditingController(text: widget.config.llmApi);
    _model = TextEditingController(text: widget.config.llmModel);
    _key = TextEditingController(text: widget.config.llmKey);
  }

  @override
  void dispose() {
    for (final c in [_project, _app, _python, _api, _model, _key]) {
      c.dispose();
    }
    super.dispose();
  }

  void _save() {
    final project = _project.text.trim();
    if (!ProjectResolver.isProject(project)) {
      setState(() => _error = '该目录下没有 zh_patch.py');
      return;
    }
    Navigator.pop(
      context,
      GuiConfig(
        projectDir: project,
        appPath: _app.text.trim(),
        pythonPath: _python.text.trim(),
        llmApi: _api.text.trim(),
        llmModel: _model.text.trim(),
        llmKey: _key.text.trim(),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return AlertDialog(
      title: const Text('设置'),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _label('工程目录（含 zh_patch.py）'),
              _pathField(_project, () async {
                final p = await pickFolder('选择汉化工程目录');
                if (p != null) setState(() => _project.text = p);
              }),
              const SizedBox(height: 14),
              _label('Claude.app 路径（留空自动探测）'),
              _pathField(_app, () async {
                final p = await pickApp('选择 Claude.app');
                if (p != null) setState(() => _app.text = p);
              }),
              const SizedBox(height: 14),
              _label('Python 解释器（留空自动探测，需 ≥ 3.10）'),
              _pathField(_python, () async {
                final p = await pickFile('选择 python3');
                if (p != null) setState(() => _python.text = p);
              }),
              const SizedBox(height: 20),
              Text('LLM 补译（可选）',
                  style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      color: scheme.onSurface.withValues(alpha: .7))),
              const SizedBox(height: 10),
              _label('API Base URL'),
              TextField(
                controller: _api,
                decoration: const InputDecoration(
                  hintText: 'http://127.0.0.1:1234/v1',
                  isDense: true,
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _label('模型名'),
                        TextField(
                          controller: _model,
                          decoration: const InputDecoration(
                            hintText: 'qwen2.5-7b-instruct',
                            isDense: true,
                            border: OutlineInputBorder(),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _label('API Key（可选）'),
                        TextField(
                          controller: _key,
                          obscureText: true,
                          decoration: const InputDecoration(
                            hintText: 'sk-…',
                            isDense: true,
                            border: OutlineInputBorder(),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(_error!, style: TextStyle(color: scheme.error, fontSize: 12)),
              ],
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context), child: const Text('取消')),
        FilledButton(onPressed: _save, child: const Text('保存')),
      ],
    );
  }

  Widget _label(String text) => Padding(
        padding: const EdgeInsets.only(bottom: 5),
        child: Text(text,
            style: TextStyle(
                fontSize: 11.5,
                color: Theme.of(context)
                    .colorScheme
                    .onSurface
                    .withValues(alpha: .6))),
      );

  Widget _pathField(TextEditingController c, VoidCallback onPick) {
    return Row(
      children: [
        Expanded(
          child: TextField(
            controller: c,
            style: const TextStyle(fontSize: 12.5),
            decoration: const InputDecoration(
                isDense: true, border: OutlineInputBorder()),
          ),
        ),
        const SizedBox(width: 8),
        OutlinedButton(onPressed: onPick, child: const Text('选择')),
      ],
    );
  }
}
