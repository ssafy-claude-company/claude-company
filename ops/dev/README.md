# dev 서비스 — 피드백 + 코드 지도 (제품 아님)

피드백 기능과 코드 지도를 murmur 제품에서 분리한 **개발 영역 서비스**다(2026-07-11 사용자 지시
"피드백 기능 합쳐서 외부로 빼버려"). murmur는 화면의 핀 오버레이(클라이언트)만 남기고,
백엔드·백로그·코드지도는 전부 여기서 산다.

## 구성

```
ops/dev/
  run.sh                  # 기동: 127.0.0.1:8100 (nginx /dev/ 프록시 대상)
  scan.py                 # 코드 지도 데이터 재생성 → static/codegraph.json
  migrate_from_murmur.py  # murmur pg → sqlite 피드백 데이터 1회 이사(멱등)
  app/                    # Django: config + feedback 앱(murmur에서 이식)
  static/backlog.html     # / — 피드백 백로그(대기·처리됨·완료, 서비스 필터)
  static/codegraph.html   # /codegraph/ — 코드 지도(힘 기반 그래프)
  static/fb.js            # 공용 핀 레이어 — 페이지의 모든 요소에 핀(murmur fbdom 셀렉터 이식)
```

- **지도 배치 = 힘 기반(결정론)**: 연결(import)이 위치를 만든다 — 가까움 = 실제로 엮임.
  해시 시드라 로드마다 같은 그림. 축소하면 라벨을 숨겨 형태만 보인다.
- **핀은 모든 요소에**: 노드뿐 아니라 지도·백로그 페이지의 어떤 컴포넌트든 📌 모드에서 클릭해
  핀을 단다(백로그 자체 핀은 service=dev).

- **DB**: sqlite `ops/var/devfeedback.sqlite3` (env `DEV_FEEDBACK_DB`).
- **인증**(feedback/auth.py — 이식 계약의 어댑터): ①`DEV_FEEDBACK_TOKENS="토큰:handle,…"`(자립)
  ②`DEV_FEEDBACK_MURMUR=http://127.0.0.1:8000`이면 murmur `/api/me/`에 위임 — 브라우저의
  murmur admin 토큰(organt_token)이 그대로 통한다(같은 오리진 /dev/ 서빙 전제).
- **service 격리**: murmur 화면 핀 = `murmur`, 코드 지도 핀 = `codegraph`. 백로그는 통합 뷰(`?service=all`).

## 운영 루프 (변경 없음 — 호스트만 이사)

admin이 핀 → 세션이 `app/manage.py feedback_backlog`로 읽고 고침 → `feedback_resolve <id> --note …`
→ admin이 백로그에서 완료(닫기). 코드가 바뀌면 `python3 ops/dev/scan.py`로 지도 갱신.

## 검증 기록 (2026-07-11)

대본: `manage.py test feedback` 12개(dev 2 통과 + murmur 통합 10 skip — 이식 계약상 정상).
실기동: sqlite + 정적 토큰으로 8199 기동, 실 API로 핀 생성·처리·댓글·백로그·지도 렌더 확인.
