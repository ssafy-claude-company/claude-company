# dev — 메타 서비스 (프로젝트 지도·논리층·피드백 플랫폼)

murmur·claude-company 전용 도구가 아니다. **claude company가 만드는 모든 product**(봇 산출물
포함)를 등록해 같은 능력을 얹는 플랫폼이다: 스캔→코드 지도, 논리(추상) 레이어, 모든 요소
피드백 핀. 데이터는 전부 DB — 정적 파일·일회용 스크립트 없음(2026-07-11 사용자 방향).

## 표면

| 경로 | 무엇 |
|---|---|
| `/` | 프로젝트 목록·등록·재스캔 |
| `/p/<slug>/` | 캔버스 허브(프로젝트당 여러 장) |
| `/p/<slug>/c/<cid>/` | **공유 캔버스** — (사람의 간단한 인터랙션+스케치) + (AI의 문서화/시각화). 더블클릭=스티키 즉시 타이핑, 박스, ● 핸들 드래그=연결선, 인라인 라벨, Del·Ctrl+Z, 휠=이동·Ctrl+휠=줌. AI는 같은 items/links API(origin=ai)로 되그린다(4초 폴링으로 나타남) |
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

제품 정의(2026-07-11 사용자): **(사용자의 간단한 인터랙션+스케치) + (AI의 문서화/시각화)** —
사람과 AI 조직이 만나는 공유 캔버스. 사람은 대충 그리고 핀으로 시키고, AI가 문서화해 되그린다.
graph의 Node/Edge/스캐너는 AI가 문서화할 때 쓰는 후방 근거(사용자 대면 지도 표면은 폐지).
