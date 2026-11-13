import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// GUI 侧配置，保存在 ~/.config/claude-zh-patch/gui.json
class GuiConfig {
  String projectDir;
  String appPath;
  String pythonPath;
  String protocol; // openai | anthropic
  String llmApi;
  String llmModel;
  String llmKey;

  GuiConfig({
    this.projectDir = '',
    this.appPath = '',
    this.pythonPath = '',
    this.protocol = 'openai',
    this.llmApi = '',
    this.llmModel = '',
    this.llmKey = '',
  });

  factory GuiConfig.fromJson(Map<String, dynamic> j) => GuiConfig(
        projectDir: (j['projectDir'] ?? '') as String,
        appPath: (j['appPath'] ?? '') as String,
        pythonPath: (j['pythonPath'] ?? '') as String,
        protocol: (j['protocol'] ?? 'openai') as String,
        llmApi: (j['llmApi'] ?? '') as String,
        llmModel: (j['llmModel'] ?? '') as String,
        llmKey: (j['llmKey'] ?? '') as String,
      );

  Map<String, dynamic> toJson() => {
        'projectDir': projectDir,
        'appPath': appPath,
        'pythonPath': pythonPath,
        'protocol': protocol,
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

/// 找到汉化引擎所在目录（含 zh_patch.py）。
///
/// 三种来源，按优先级：
///   1. 仓库模式：环境变量 / 上次配置 / 常见克隆位置，方便开发
///   2. 内置引擎：release 包把引擎打在 `<App>.app/Contents/Resources/engine`，
///      首次启动复制到 Application Support（app 包内不能写，会破坏签名）
///   3. 手动选择：设置里点「选择」指定任意目录
class ProjectResolver {
  static const engineDirName = 'engine';

  /// 内置引擎在运行期使用的可写副本
  static String get runtimeDir =>
      '${Platform.environment['HOME'] ?? '.'}/Library/Application Support'
      '/claude-zh-patch/$engineDirName';

  static bool isProject(String path) =>
      path.isNotEmpty && File('$path/zh_patch.py').existsSync();

  /// 是否为内置引擎的副本（界面据此显示「内置引擎」而不是一串路径）
  static bool isBundled(String path) =>
      path.isNotEmpty && path == runtimeDir;

  /// release 包里随 app 一起分发的引擎目录；开发/`flutter test` 下为 null
  static String? bundledDir() {
    final exe = File(Platform.resolvedExecutable);
    final contents = exe.parent.parent; // …/Contents
    final dir = '${contents.path}/Resources/$engineDirName';
    return isProject(dir) ? dir : null;
  }

  /// 仓库里已有的引擎目录（用户 clone 过仓库的情况）
  static String? resolve(String saved) {
    final env = Platform.environment['CLAUDE_ZH_PROJECT'] ?? '';
    final home = Platform.environment['HOME'] ?? '';
    final candidates = <String>[
      if (env.isNotEmpty) env,
      if (saved.isNotEmpty && !isBundled(saved)) saved,
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

  /// 把内置引擎安装/更新到可写副本，返回可用的引擎目录。
  /// 版本没变就直接复用，避免每次启动都复制 3 MB 数据。
  static Future<String?> prepareBundled() async {
    final src = bundledDir();
    if (src == null) return isProject(runtimeDir) ? runtimeDir : null;

    final dst = runtimeDir;
    final srcVer = _version(src);
    final dstVer = _version(dst);
    if (srcVer != null && srcVer == dstVer && isProject(dst)) return dst;

    try {
      await _install(src, dst);
    } catch (_) {
      // 复制失败不致命：只要目标里还有可用的引擎就继续用旧版
      if (isProject(dst)) return dst;
      return null;
    }
    return isProject(dst) ? dst : null;
  }

  static String? _version(String dir) {
    try {
      final f = File('$dir/ENGINE_VERSION');
      if (!f.existsSync()) return null;
      return f.readAsStringSync().trim();
    } catch (_) {
      return null;
    }
  }

  /// 引擎升级时保留用户跑出来的东西：翻译成果与进度往前备一份。
  static const _userData = [
    'zh-Hans-final.json',
    'translate_done.json',
    'icu_done.json',
    'translate_queue.json',
  ];

  static Future<void> _install(String src, String dst) async {
    final srcDir = Directory(src);
    final dstDir = Directory(dst);
    await dstDir.create(recursive: true);

    final stamp = DateTime.now()
        .toIso8601String()
        .replaceAll(RegExp(r'[^0-9]'), '')
        .substring(0, 14);
    final backupDir = Directory('${dstDir.parent.path}/backups/$stamp');

    for (final entity in srcDir.listSync(followLinks: false)) {
      if (entity is! File) continue;
      final name = entity.uri.pathSegments.last;
      final target = File('$dst/$name');
      if (target.existsSync() &&
          _userData.contains(name) &&
          target.lengthSync() > 0) {
        await backupDir.create(recursive: true);
        await target.copy('${backupDir.path}/$name');
      }
      await entity.copy(target.path);
      if (name.endsWith('.sh')) {
        await Process.run('chmod', ['755', target.path]);
      }
    }
  }
}


/// 找一个可用的 Python（≥3.9，系统自带 / Xcode CLT 的 3.9.6 即可）。
/// GUI 从 Finder 启动时 PATH 很短，所以除了 PATH 还会问登录 shell 和常见安装位置。
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
      if (major > 3 || (major == 3 && minor >= 9)) {
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


/// 拉取模型列表（{base}/models），供设置对话框使用。
class ModelList {
  final List<String> models;
  final String? error;
  const ModelList(this.models, {this.error});
}

String normalizeBaseUrl(String input) {
  var base = input.trim().replaceAll(RegExp(r'/+$'), '');
  for (final suffix in ['/chat/completions', '/messages']) {
    if (base.endsWith(suffix)) {
      base = base.substring(0, base.length - suffix.length);
    }
  }
  if (base.isEmpty) return '';
  if (!base.endsWith('/v1')) base = '$base/v1';
  return base;
}

Future<ModelList> fetchModelList({
  required String protocol,
  required String baseUrl,
  required String apiKey,
  Duration timeout = const Duration(seconds: 12),
}) async {
  final base = normalizeBaseUrl(baseUrl);
  if (base.isEmpty) {
    return const ModelList([], error: '请先填写 Base URL');
  }
  final uri = Uri.tryParse('$base/models');
  if (uri == null || !(uri.scheme == 'http' || uri.scheme == 'https')) {
    return const ModelList([], error: 'Base URL 需要以 http:// 或 https:// 开头');
  }
  final client = HttpClient();
  try {
    final req = await client.getUrl(uri).timeout(timeout);
    req.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (apiKey.isNotEmpty) {
      if (protocol == 'anthropic') {
        req.headers.set('x-api-key', apiKey);
        req.headers.set('anthropic-version', '2023-06-01');
      } else {
        req.headers.set('Authorization', 'Bearer $apiKey');
      }
    }
    final resp = await req.close().timeout(timeout);
    final body = await resp.transform(utf8.decoder).join();
    if (resp.statusCode != 200) {
      final snippet = body.length > 160 ? body.substring(0, 160) : body;
      return ModelList([], error: 'HTTP ${resp.statusCode}: $snippet');
    }
    final data = jsonDecode(body);
    final raw = data is Map ? (data['data'] ?? data['models']) : data;
    final models = <String>[];
    if (raw is List) {
      for (final item in raw) {
        if (item is Map) {
          final id = item['id'] ?? item['name'];
          if (id is String && id.isNotEmpty) models.add(id);
        } else if (item is String && item.isNotEmpty) {
          models.add(item);
        }
      }
    }
    models.sort();
    if (models.isEmpty) {
      return const ModelList([], error: '接口没有返回模型');
    }
    return ModelList(models);
  } catch (e) {
    return ModelList([], error: '请求失败：$e');
  } finally {
    client.close(force: true);
  }
}
