"""[P0 로그 로테이션(2026-07-18, HA 설계)] append-only JSONL(audit.jsonl·flow.jsonl)은 무한 증가해
디스크를 채운다(장기 운영의 조용한 장애). 크기 상한을 넘으면 `.1..keep`로 밀어내고 새 파일을 연다.
매 write마다 stat하지 않고(오버헤드) 카운터로 주기 검사한다. 실패는 조용히 무시 — 로깅이 절대
서비스를 못 막게(로테이션 실패가 기록 실패로 번지지 않게)."""
import os


def _max_bytes():
    try:
        return int(os.environ.get("ORGANT_LOG_MAX_MB", "50")) * 1024 * 1024
    except ValueError:
        return 50 * 1024 * 1024


def rotate_if_needed(path, keep=3):
    """path가 상한 초과면 path.1..keep로 밀어내고 path를 비운다(원자적 rename). 무해 실패."""
    cap = _max_bytes()
    if cap <= 0:
        return
    try:
        if os.path.getsize(path) < cap:
            return
    except OSError:
        return
    try:
        oldest = f"{path}.{keep}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(keep - 1, 0, -1):
            src = f"{path}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{path}.{i + 1}")
        os.replace(path, f"{path}.1")
    except OSError:
        pass


class RotatingCounter:
    """N회마다 로테이션을 검사하는 경량 게이트(매 write stat 회피). 스레드/태스크 경합엔 관대 —
    카운터가 정확할 필요 없이 '가끔 검사'면 충분(상한은 근사 유지)."""
    def __init__(self, path, every=256, keep=3):
        self.path = str(path)
        self.every = max(1, int(every))
        self.keep = keep
        self._n = 0

    def tick(self):
        self._n += 1
        if self._n >= self.every:
            self._n = 0
            rotate_if_needed(self.path, self.keep)
