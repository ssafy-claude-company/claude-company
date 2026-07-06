# guide — 전송 구현체(Guide) 레포 표지판

이 레포는 ClaudeCompany의 **전송기(Guide) 계층**이다. Guide는 흐름(Flow/SYS)을 모르고
전송·조회만 한다. 추상(계약)은 system 쪽에 있고, 여기는 매체별 구현체만 둔다.

## 파일 지도 — 무엇이 라이브이고 무엇이 아닌가

| 파일 | 상태 | 설명 |
|---|---|---|
| `murmur_guide.py` (**MurmurGuide**) | **라이브 클라이언트** | murmur 서버의 guide_bridge API로 말하는 HTTPS 전송기. Phase 2 라이브 러너가 쓰는 실물. 서버 짝: `murmur/backend/sns/guide_bridge.py` |
| `discord_guide.py` (DiscordGuide) | 비검증 — Discord 시대 산물 | Phase 1(Discord SMS) 구현체. 현재 라이브 경로 아님, 회귀 검증도 안 됨 |
| `discord_main.py` | 비검증 — Discord 시대 산물 | Discord 리스너 엔트리포인트(SYS 가동). 위와 동일 |
| `channels.py` | Discord 보조 | 채널 해석 유틸(DiscordGuide 계열). PJT 테스트(`tests/test_channels.py`)가 참조 |
| `requirements.txt` | — | 매체 SDK 의존성 |

## 조사 에이전트를 위한 경고 — stale 산출물 오도 전례

- **옛 이름 함정**: MurmurGuide의 옛 이름은 `HttpSnsGuide`였다. 과거 git 인덱스에 남은
  stale `.pyc`(`__pycache__/http_sns_guide.cpython-*.pyc`)와 주석의 옛 이름이 조사
  에이전트를 "HttpSnsGuide라는 모듈이 존재한다"로 오도한 실제 전례가 있다.
  `__pycache__`/`*.pyc`는 진실원이 아니다 — 반드시 `.py` 소스로만 판단하라.
- **계층 규칙**: 이름·배포가 구현체에 결합되면 안 된다. 추상↔구현 방향은
  system(추상) → guide(구현). Discord 구현체는 비검증 상태임을 전제로 다뤄라.

## 서버 짝(원격 계약)

MurmurGuide ↔ `murmur/backend/sns/guide_bridge.py` (Django, Render 배포:
organt-sns.onrender.com). 계약(post/send_request/send_response/open_task/
update_status/edit_message/read_thread/…)을 바꿀 땐 양쪽을 함께 봐야 한다.
