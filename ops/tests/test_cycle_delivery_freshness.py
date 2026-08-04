"""[낡은 배포는 배달이 아니다(2026-08-04, 사용자: '마일스톤 끝날때마다 배포 되어서 사용자 실측
도와야 하는거 아니야?')]

실측 U-478: MS-2에서 배포(08-01 22:00)한 neon-dodge 주소가, MS-3(08-03)에서 320×568 터치 결함을
고친 뒤에도 '[마일스톤 보고] … 바로 열어 확인'으로 그대로 실렸다 — 사용자 실측을 돕기는커녕
고치기 전 빌드를 열게 했다. 배포 주소를 가진 판에서 그 배포 이후 산출물이 바뀌었으면 주기는
재배포 없이 닫히지 않는다."""
import os, sys, time

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import cycle_delivery_error


class _Flow:
    def __init__(self, ws):
        self.workspace = ws
        self._deploy_url = "https://neon-dodge.example.com"
        self._deploy_ts = time.time() - 3600      # 1시간 전 배포
        self.milestones = []
        self.log = None


def _ws(tmp_path, mtime_ago):
    ws = tmp_path / "w"
    (ws / "public").mkdir(parents=True)
    f = ws / "public" / "index.html"
    f.write_text("<html>game</html>")
    t = time.time() - mtime_ago
    os.utime(f, (t, t))
    return str(ws)


def test_배포_후_바뀐_산출물은_재배포_없이_안_닫힌다(tmp_path):
    flow = _Flow(_ws(tmp_path, mtime_ago=60))     # 배포(1h 전) 후에 수정됨
    err = cycle_delivery_error(flow)
    assert err and "재배포" in err and flow._deploy_url in err


def test_배포가_최신이면_그대로_닫힌다(tmp_path):
    flow = _Flow(_ws(tmp_path, mtime_ago=7200))   # 산출물이 배포보다 오래됨(변경 없음)
    assert cycle_delivery_error(flow) == ""


def test_배포_이력이_없는_판은_종전_관문_그대로(tmp_path):
    """처음부터 배포가 없던 판은 이 검사가 끼어들지 않는다 — index.html이 있으면 종전대로 통과
    (완성작 버튼이 작업공간을 직접 서빙하므로 사용자는 최신을 본다)."""
    flow = _Flow(_ws(tmp_path, mtime_ago=60))
    flow._deploy_url = ""
    flow._deploy_ts = 0
    assert cycle_delivery_error(flow) == ""
