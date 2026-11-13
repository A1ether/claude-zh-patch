import 'package:flutter_test/flutter_test.dart';
import 'package:claude_zh_gui/service.dart';

void main() {
  test('parseStatus 能容忍前置输出', () {
    final s = parseStatus('warning: x\n{"app_version": "1.2.3"}');
    expect(s?['app_version'], '1.2.3');
  });

  test('parseStatus 对无效输入返回 null', () {
    expect(parseStatus(null), isNull);
    expect(parseStatus('not json'), isNull);
  });

  test('GuiConfig 往返序列化', () {
    final c = GuiConfig(projectDir: '/tmp/p', appPath: '/Applications/Claude.app');
    final d = GuiConfig.fromJson(c.toJson());
    expect(d.projectDir, '/tmp/p');
    expect(d.appPath, '/Applications/Claude.app');
  });
}
