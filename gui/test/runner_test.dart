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
}
