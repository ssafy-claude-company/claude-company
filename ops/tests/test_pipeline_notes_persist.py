"""[마디 마커는 유실되면 안 된다(2026-08-04, 실측 U-478 MS-755549625-3)]

_pnote는 봇 도구 호출 뒤에만 채널로 flush된다 — 그 사이 러너가 재시작하면 '[마일스톤 시작]' 같은
경계 마커가 메모리와 함께 증발한다. 실측: MS-3은 ms_open 로그는 있는데 시작 마커 게시가 없어
화면의 홈 블록이 서지 못했고, 그 주기의 SubTask 완수·iter 검증·완수 기록 전부가 집을 잃었다
(조용히 사라지거나 열려 있던 남의 주기 상자에 얹힘). 마커를 스냅샷에 영속한다."""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
import inspect
from system import sys_recovery


def test_스냅샷이_파이프라인_마커를_영속한다():
    src = inspect.getsource(sys_recovery)
    assert '"pipeline_notes"' in src, "스냅샷에 파이프라인 마커가 없다 — 재시작이 마디 마커를 증발시킨다"
    assert '"pnote_seen"' in src, "중복 억제 이력이 영속되지 않으면 복구 후 같은 마디가 두 번 게시된다"


def test_복원이_마커를_잃지_않고_합친다():
    src = inspect.getsource(sys_recovery)
    i = src.index('snap.get("pipeline_notes")')
    seg = src[i - 200:i + 400]
    assert "_pipeline_notes" in seg
    # 덮어쓰기가 아니라 합류 — 복원 시점에 이미 쌓인 새 마커를 지우면 그건 또 다른 유실이다
    assert "+" in seg or "extend" in seg
