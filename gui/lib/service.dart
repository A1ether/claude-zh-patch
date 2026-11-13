import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// GUI 侧配置，保存在 ~/.config/claude-zh-patch/gui.json
class GuiConfig {
  String projectDir;
  String appPath;
  String pythonPath;
  String llmApi;
  String llmModel;
  String llmKey;

  GuiConfig({
    this.projectDir = '',
    this.appPath = '',
    this.pythonPath = '',
    this.llmApi = '',
    this.llmModel = '',
    this.llmKey = '',
  });

  factory GuiConfig.fromJson(Map<String, dynamic> j) => GuiConfig(
        projectDir: (j['projectDir'] ?? '') as String,
        appPath: (j['appPath'] ?? '') as String,
        pythonPath: (j['pythonPath'] ?? '') as String,
        llmApi: (j['llmApi'] ?? '') as String,
        llmModel: (j['llmModel'] ?? '') as String,
        llmKey: (j['llmKey'] ?? '') as String,
      );

  Map<String, dynamic> toJson() => {
        'projectDir': projectDir,
        'appPath': appPath,
        'pythonPath': pythonPath,
        'llmApi': llmApi,
        'llmModel': llmModel,
        'llmKey': llmKey,
      };

  GuiConfig copy() => GuiConfig.fromJson(toJson());
}

class ConfigStore {
  static String get dir =>
      '${Platform.environment['HOME'] ?? '.'}/.config/claude-zh-patch';

  static File get file => File('$dir/gui.json');

  static GuiConfig load() {
    try {
      final f = file;
      if (!f.existsSync()) return GuiConfig();
      return GuiConfig.fromJson(
          jsonDecode(f.readAsStringSync()) as Map<String, dynamic>);
    } catch (_) {
      return GuiConfig();
    }
  }

  static Future<void> save(GuiConfig cfg) async {
    try {
      await Directory(dir).create(recursive: true);
      await file.writeAsString(
          const JsonEncoder.withIndent('  ').convert(cfg.toJson()));
    } catch (_) {
      // 配置写不进去不影响主流程
    }
  }
}

/// 找到补丁工程目录（含 zh_patch.py）。
class ProjectResolver {
  static bool isProject(String path) =>
      path.isNotEmpty && File('$path/zh_patch.py').existsSync();

  static String? resolve(String saved) {
    final env = Platform.environment['CLAUDE_ZH_PROJECT'] ?? '';
    final home = Platform.environment['HOME'] ?? '';
    final candidates = <String>[
      if (env.isNotEmpty) env,
      if (saved.isNotEmpty) saved,
    ];

    // 从当前目录一路往上找（flutter run 时 cwd 是 gui/）
    Directory dir = Directory.current;
    for (var i = 0; i < 5; i++) {
      candidates.add(dir.path);
      if (dir.parent.path == dir.path) break;
      dir = dir.parent;
    }

    candidates.add('$home/claude-zh-patch');
    candidates.add('$home/Documents/claude-zh-patch');
    candidates.add('$home/Desktop/claude-zh-patch');

    for (final c in candidates) {
      if (isProject(c)) return c;
    }
    return null;
  }
}

/// 找一个可用的 Python（≥3.10）。GUI 从 Finder 启动时 PATH 很短，
/// 所以除了 PATH 还会问登录 shell 和常见安装位置。
class PythonRuntime {
  static String? _cache;

  static String? get cached => _cache;

  static Future<String?> resolve({String preferred = ''}) async {
    if (_cache != null) return _cache;

    final candidates = <String>[
      if (preferred.isNotEmpty) preferred,
      if ((Platform.environment['PYTHON'] ?? '').isNotEmpty)
        Platform.environment['PYTHON']!,
      '/opt/homebrew/bin/python3',
      '/usr/local/bin/python3',
      '/usr/bin/python3',
      'python3',
    ];

    // 登录 shell 里的 python3（能读到用户的 PATH 配置）
    for (final shell in ['/bin/zsh', '/bin/bash']) {
      try {
        final r = await Process.run(shell, ['-lc', 'command -v python3'])
            .timeout(const Duration(seconds: 5));
        final p = (r.stdout as String).trim().split('\n').first.trim();
        if (p.isNotEmpty) candidates.insert(0, p);
      } catch (_) {}
    }

    final seen = <String>{};
    for (final c in candidates) {
      if (!seen.add(c)) continue;
      final version = await _version(c);
      if (version == null) continue;
      final parts = version.split('.');
      if (parts.length < 2) continue;
      final major = int.tryParse(parts[0]) ?? 0;
      final minor = int.tryParse(parts[1]) ?? 0;
      if (major > 3 || (major == 3 && minor >= 10)) {
        _cache = c;
        return c;
      }
    }
    return null;
  }

  static Future<String?> _version(String exe) async {
    try {
      final r = await Process.run(exe, ['-V'])
          .timeout(const Duration(seconds: 5));
      if (r.exitCode != 0) return null;
      final out = '${r.stdout}${r.stderr}';
      final m = RegExp(r'(\d+\.\d+(?:\.\d+)?)').firstMatch(out);
      return m?.group(1);
    } catch (_) {
      return null;
    }
  }
}

class LogLine {
  final String text;
  final bool stderr;
  const LogLine(this.text, {this.stderr = false});
}

typedef LineSink = void Function(LogLine line);

/// 以子进程方式跑补丁命令，逐行回传输出。
class TaskRunner {
  Process? _proc;

  bool get busy => _proc != null;

  Future<int> run(
    String executable,
    List<String> args, {
    required String cwd,
    required Map<String, String> env,
    required LineSink onLine,
  }) async {
    final proc = await Process.start(executable, args,
        workingDirectory: cwd, environment: env, includeParentEnvironment: true);
    _proc = proc;

    final stdoutDone = proc.stdout
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .forEach((l) => onLine(LogLine(l)))
        .catchError((_) {});
    final stderrDone = proc.stderr
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .forEach((l) => onLine(LogLine(l, stderr: true)))
        .catchError((_) {});

    final code = await proc.exitCode;
    await Future.wait([stdoutDone, stderrDone]);
    _proc = null;
    return code;
  }

  void cancel() {
    final p = _proc;
    if (p == null) return;
    p.kill(ProcessSignal.sigint);
    Future.delayed(const Duration(seconds: 2), () {
      if (_proc != null) p.kill(ProcessSignal.sigkill);
    });
  }

  /// 只读查询：跑完拿全部 stdout。
  static Future<String?> capture(
    String executable,
    List<String> args, {
    required String cwd,
    required Map<String, String> env,
    Duration timeout = const Duration(seconds: 40),
  }) async {
    try {
      final r = await Process.run(executable, args,
              workingDirectory: cwd,
              environment: env,
              includeParentEnvironment: true)
          .timeout(timeout);
      if (r.exitCode != 0) return null;
      return r.stdout as String;
    } catch (_) {
      return null;
    }
  }
}

/// 调用 osascript 打开 macOS 原生选择框。
Future<String?> pickFolder(String prompt) async {
  final r = await Process.run('osascript', [
    '-e',
    'POSIX path of (choose folder with prompt "$prompt")',
  ]);
  if (r.exitCode != 0) return null;
  var p = (r.stdout as String).trim();
  if (p.length > 1 && p.endsWith('/')) p = p.substring(0, p.length - 1);
  return p.isEmpty ? null : p;
}

Future<String?> pickApp(String prompt) async {
  final r = await Process.run('osascript', [
    '-e',
    'POSIX path of (choose application with prompt "$prompt")',
  ]);
  if (r.exitCode != 0) return null;
  final p = (r.stdout as String).trim();
  return p.isEmpty ? null : p;
}

Future<String?> pickFile(String prompt) async {
  final r = await Process.run('osascript', [
    '-e',
    'POSIX path of (choose file with prompt "$prompt")',
  ]);
  if (r.exitCode != 0) return null;
  final p = (r.stdout as String).trim();
  return p.isEmpty ? null : p;
}

Map<String, dynamic>? parseStatus(String? raw) {
  if (raw == null) return null;
  try {
    final i = raw.indexOf('{');
    if (i < 0) return null;
    return jsonDecode(raw.substring(i)) as Map<String, dynamic>;
  } catch (_) {
    return null;
  }
}
