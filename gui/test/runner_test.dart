import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:claude_zh_gui/service.dart';

void main() {
  test('TaskRunner 逐行回传输出', () async {
    final lines = <String>[];
    final code = await TaskRunner().run(
      '/bin/bash',
      ['-c', 'echo one; echo two'],
      cwd: Directory.systemTemp.path,
      env: const {},
      onLine: (l) => lines.add(l.text),
    );
    expect(code, 0);
    expect(lines, containsAll(['one', 'two']));
  });

  test('TaskRunner.capture 返回完整 stdout', () async {
    final out = await TaskRunner.capture(
      '/bin/echo',
      ['{"app_version": "9.9.9"}'],
      cwd: Directory.systemTemp.path,
      env: const {},
    );
    expect(parseStatus(out)?['app_version'], '9.9.9');
  });

  test('ProjectResolver 识别工程目录', () {
    expect(ProjectResolver.isProject('/definitely/not/here'), isFalse);
  });

  test('ProjectResolver 内置引擎副本路径', () {
    final dir = ProjectResolver.runtimeDir;
    expect(dir, endsWith('/Library/Application Support/claude-zh-patch/engine'));
    expect(ProjectResolver.isBundled(dir), isTrue);
    expect(ProjectResolver.isBundled('/tmp/claude-zh-patch'), isFalse);
    expect(ProjectResolver.isBundled(''), isFalse);
  });

  test('normalizeBaseUrl 归一化', () {
    expect(normalizeBaseUrl('http://h:1'), 'http://h:1/v1');
    expect(normalizeBaseUrl('http://h:1/v1/'), 'http://h:1/v1');
    expect(normalizeBaseUrl('http://h:1/v1/chat/completions'),
        'http://h:1/v1');
    expect(normalizeBaseUrl('http://h:1/messages'), 'http://h:1/v1');
  });

  test('fetchModelList 请求 /models 并解析', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    var seenPath = '';
    var seenAuth = '';
    server.listen((req) async {
      seenPath = req.uri.path;
      seenAuth = req.headers.value('authorization') ?? '';
      req.response.headers.contentType = ContentType.json;
      req.response.write(jsonEncode({
        'data': [
          {'id': 'model-b'},
          {'id': 'model-a'},
        ]
      }));
      await req.response.close();
    });
    final res = await fetchModelList(
      protocol: 'openai',
      baseUrl: 'http://127.0.0.1:${server.port}',
      apiKey: 'k1',
    );
    expect(seenPath, '/v1/models');
    expect(seenAuth, 'Bearer k1');
    expect(res.models, ['model-a', 'model-b']);
    expect(res.error, isNull);
    await server.close(force: true);
  });
}
