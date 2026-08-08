"""[스위트가 통째로 안 돌면 계약도 안 지켜진다(2026-08-07)] `ops/tests`의 네 파일이 수집 단계에서
죽어 있었다 — `from organt_discord.main import …`(모듈은 `ops/organt_discord/`에 **있다**)와
`from tests.test_milestone import _flow`. 저장소 루트만 sys.path에 있어서 `ops/`가 안 보였던 것이고,
그동안 이 파일들은 `--ignore`로 건너뛰며 돌았다(= 계약 15건이 아무도 안 재고 있었다).

경로는 테스트가 각자 고칠 것이 아니라 수집기가 한 번 세울 것이다.
"""
import os
import sys

_OPS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_OPS)
for _p in (_ROOT, _OPS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
