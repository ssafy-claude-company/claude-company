# dev — 메타 서비스 (프로젝트 지도·논리층·피드백 플랫폼)

murmur·claude-company 전용 도구가 아니다. **claude company가 만드는 모든 product**(봇 산출물
포함)를 등록해 같은 능력을 얹는 플랫폼이다: 스캔→코드 지도, 논리(추상) 레이어, 모든 요소
피드백 핀. 데이터는 전부 DB — 정적 파일·일회용 스크립트 없음(2026-07-11 사용자 방향).

## 표면

| 경로 | 무엇 |
|---|---|
| `/` | 프로젝트 목록·등록·재스캔 |
| `/p/<slug>/` | 프로젝트 지도 — 논리 레이어(편집 가능) ⇄ 소스 레이어(d3 force) |
| `/p/<slug>/feedback` | 프로젝트 백로그 · `/feedback` 전체 백로그 |
| `/api/projects/…` | 등록·스캔·그래프·논리층(PUT=UI 편집 저장) |
| `/api/feedback/…` | 피드백(이식 계약 — service=프로젝트 슬러그) |

## 구조

- `app/graph/` — Project·ScanRun(스냅샷 JSON)·Concept·ConceptEdge + 범용 스캐너(py ast·js/vue
  상대 import, 언어 하드코딩 없음) + API. `app/feedback/` — murmur에서 이식한 피드백 앱.
- `static/` — index·map·backlog(페이지 자산) + fb.js(모든 요소 핀 레이어) + vendor/d3.
  `logical.json`은 **시드 전용**(seed_projects가 1회 흡수 — 이후 정본은 DB).
- 인증: admin 토큰(정적 env 또는 murmur `/api/me` 위임). DB: sqlite `ops/var/devfeedback.sqlite3`.

## 운영

```bash
bash ops/dev/run.sh                                   # 기동(systemd dev-web이 이걸 실행)
app/manage.py seed_projects                           # 초기 시드(멱등) — 봇 산출물 자동 등재
app/manage.py test graph feedback                     # 대본 검증
app/manage.py feedback_backlog / feedback_resolve …   # 피드백 처리 루프(전 프로젝트 공용)
```

논리층은 지도 화면의 **논리 편집** 모드에서 만든다(개념·관계·소스 매핑·축) — 저장은 API로
DB에 영속. 코드가 바뀌면 화면의 **재스캔** 버튼(또는 scan API).
