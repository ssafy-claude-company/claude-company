# dev — 메타 서비스 (프로젝트 지도·논리층·피드백 플랫폼)

murmur·claude-company 전용 도구가 아니다. **claude company가 만드는 모든 product**(봇 산출물
포함)를 등록해 같은 능력을 얹는 플랫폼이다: 스캔→코드 지도, 논리(추상) 레이어, 모든 요소
피드백 핀. 데이터는 전부 DB — 정적 파일·일회용 스크립트 없음(2026-07-11 사용자 방향).

## 표면

| 경로 | 무엇 |
|---|---|
| `/` | 프로젝트 목록·등록·재스캔 |
| `/p/<slug>/` | 프로젝트 지도 — 뷰(레이어) 생성·전환, 더블클릭=노드, 패널=편집·관계, 레인 드래그 재배정 |
| `/p/<slug>/feedback` | 프로젝트 백로그 · `/feedback` 전체 백로그 |
| `/api/projects/…` | 등록·스캔·그래프 + nodes/edges/views CRUD(화면의 모든 편집이 이 API) |
| `/api/feedback/…` | 피드백(이식 계약 — service=프로젝트 슬러그) |

## 구조

- `app/graph/` — **플랫폼 원시: Node(타입 자유)·Edge(관계)·View(레이어=데이터)** + Project·
  ScanRun(이력) + 범용 스캐너. 스캔은 원시의 소비자(파일=Node origin=scan, import=Edge) —
  재스캔은 scan-원산만 동기하고 사람이 만든 노드·관계·메모·좌표는 불변. `app/feedback/` — 피드백 앱.
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

구조(레이어·축·타입)는 코드에 없다 — 전부 화면에서 만든다: [+ 뷰]로 레이어, 더블클릭으로
노드, 패널에서 관계·설명·레인·소스 매핑. 코드가 바뀌면 **재스캔**(사람 것 보존 동기).
