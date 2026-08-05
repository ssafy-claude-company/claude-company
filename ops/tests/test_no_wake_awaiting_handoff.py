"""[기다리는 사람은 깨우지 않는다(2026-08-04, 전수 대화 스캔 실측)]

위임을 걸어 둔 요청자는 SYS가 결과로 이어줄 때까지 할 일이 없다 — 그런데 릴레이 구동이 그의
in_progress 백로그를 보고 계속 깨워 '자동 위임 결과를 기다리는 중… 추가 행동 없이 마칩니다'
무도구 턴이 U-504에서 46분간 15턴, U-496에서 7턴 탔다(마감 경로 drain 가드는 이 일반 경로를
안 덮는다). 인플라이트 위임이 살아 있는 사람은 구동·차선 추가·첫 착수 대상에서 제외한다.
"""
import io as _io, sys

sys.path.insert(0, __file__.rsplit('/ops/', 1)[0])


def _src():
    import system.sys_core as m
    return _io.open(m.__file__.replace('.pyc', '.py'), encoding='utf-8').read()


def test_구동_필터가_대기자를_뺀다():
    s = _src()
    i = s.index('def _awaiting_handoff')
    seg = s[i:i+900]
    assert 'handoff_inflight' in seg and 'not _t.done()' in seg
    j = s.index('_rows = [x for x in _act_rows(flow)', i)
    assert 'not _awaiting_handoff' in s[j:j+300]


def test_첫착수와_차선추가도_대기자를_뺀다():
    s = _src()
    assert s.count('_awaiting_handoff(') >= 3   # 정의+구동필터+첫착수+차선
