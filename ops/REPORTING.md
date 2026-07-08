# REPORTING — 세션→사용자 보고 규율 (정본)

> **보고 = 위임의 반환값이다. 작업 일지가 아니다.** 독자(사용자)는 구현하지 않는다 — 방향을
> 정하고, 결과를 수용/기각하고, 다음 베팅을 정한다. 보고는 그 세 행위에 필요한 것만 담는다.
> 이 규율은 ①사용자 교정 이력(2026-07-04~05, §5 표)과 ②보고·글쓰기 정전(§7)에서 도출했다.
> 이 시스템이 봇들에게 강제하는 위임 보고 계약("[결과]/[검증]/[리스크] — 받은 쪽이 산출물을
> 재탐색하지 않아도 되게, 간결히")의 사람판이기도 하다 — 봇에게 시키는 것을 사용자에게 지켜라.

## 1. 기본형 — 완료 보고 골격 (순서 고정)

| 절 | 내용 | 근거 |
|---|---|---|
| **[결과]** | 첫 줄. **원 요구 원문 대비** 상태 — 됐다/부분/안 됐다. 자기식으로 축소한 요구가 아니라 사용자가 말한 그 문장 기준 | BLUF(미군 문서 규정)·Minto 피라미드: 결론 선행 |
| **[확인]** | 독자가 **자기 자리에서** 직접 확인하는 법(URL·화면·산출물) + 결정적 증거 1~3개 — 로그 원문·수치·트랜스크립트 인용. 형용사로 대체 금지 | Tufte(증거는 원자료로)·Toulmin(주장 옆 증거) |
| **[결정]** | 두 라벨로 구분해 써라 — **[결정 통보]**(내가 정했다 + 근거, 반대면 뒤집어라) / **[결정 요청]**(네가 골라야 한다 + **왜 내가 못 정하는지** + 독자가 모른다는 전제로 맥락 서빙). 상신 기준: 스스로 답을 내려보고, *내리면 안 되는 이유*가 있을 때만. 없으면 "없음" 한 마디. 있으면 맨 앞으로 승격 + 권고 1개와 비용 | SBAR의 R·Bezos. 교정 2026-07-08: "결정이라고만 하면 결정 한건지 하라는건지 어떻게 알아" |
| **[의미/관측]** | 독자의 좌표(연구·제품·비용)에서의 함의. 현상은 **키워드로 명명**하고 각각 증거를 붙인다 | 사용자 지시("키워드와 함께 엮어서") |
| **[미검증/반증]** | 안 한 것·못 본 갈래·이 결과를 무효화할 수 있는 것 **전부**. 미완을 '한계'라 부르지 마라 — "안 했다"로 써라 | Feynman "Cargo Cult Science"(자기 결과를 흔들 수 있는 것을 전부 먼저 밝혀라) |
| **[다음]** | 1줄 | — |

**꼬리 규칙**: 집행 디테일(병합 절차·수리 과정·테스트 개수·도구 사용기)은 **본문 금지** —
기록 위치(커밋·docs 경로)만 남긴다. 독자가 파고들 때를 독자가 정한다. (교정: "내가 구현해?
그럼 내가 구현 디테일 알아야 해?")

## 1-1. 리뷰 트레이스(PR·문서 검수물) — 검수 포인트를 그 안에 짚는다

PR처럼 **독자가 직접 검수해야 하는 산출물에는, 산출물 안에 검수 동선을 만들어 준다**(교정:
"Trace로 남는 것들은 내가 봐야 하는 부분을 focus 해줘 — PR 내에서 comment로 짚어주든"):
- **인라인 코멘트**로 판단이 필요한 지점만 번호 붙여 짚는다(`[검수 n/총]` — 무엇이 결정이고
  왜 당신 판단이 필요한지 한 줄).
- 리뷰 요약에 **동선과 예상 소요**("§1→§3, 10분")를 적고, **안 읽어도 되는 것**(원자료·기계
  산출)을 명시해 검수 범위를 줄여 준다.
- 짚는 지점은 결정·리스크·경계 판단에 한정 — 전부 짚으면 아무것도 못 짚은 것이다.
- **예외 없음**: 커밋이 작아도 검수 트레이스에는 반드시 focus를 딴다(교정: "commit 짧아서
  넘어가는데 — 무조건 focus 따놔").
- **무게 보정(독자 위치 기준)**: 검수로 올리는 것은 **새 판단**(방향·경계·연구 주장·리스크·불변식
  변화)뿐이다. 집행 세부(문서 위생·지도 등재·명령 현행화)와 **이미 내려진 결정의 문서화**는
  검수 대상이 아니다 — 그런 PR엔 `[검수 불필요 — 집행 세부]`를 머리에 박고 요약만 남긴다
  (교정: "내가 읽을 만한 무게감이 아니었다 — 내 위치와 역할을 생각하고 focus 따").

## 2. 변형 3종

- **분석 보고**(실험·피드백 요청 시): 현상 → **키워드 명명** → 증거(원문 인용) → 함의.
  '좋았던 것 / 구조가 막은 것 / 특이점'을 구분한다. 특이점은 후속 관측 항목으로 승격.
- **중간 보고**: ≤3줄 — 무엇이 돌고 있고, 다음 신호가 무엇이며 언제 오는지. 그 이상 쓰지 마라.
- **차단 보고**(결정 필요): 선택지 2~3 + 권고 + 각각의 비용/되돌림. **결정 요구를 뒤에 묻지
  마라** — 첫 줄이다. (교정: 라이브 전환 결정을 세 번 추궁 뒤에야 꺼낸 실패)

## 3. 문장 규율

- **가독성이 최우선이다** — 문장은 짧게. 한 문단 = 한 생각. 밀도 높은 벽문단·겹문장·수사 장식
  금지. 훑어서 구조가 보여야 한다. (교정 2026-07-08: "다음에도 이따구로 보고하면 안됨 —
  가독성부터 해서 걍 구림")
- **쉬운 말로 써라** — 어려운 단어·학술 용어로 말하지 마라. 같은 뜻이면 평범한 말을 고른다.
  (교정 2026-07-08: "말 할때 복잡하게 어려운 단어 쓰지마")
- **세세한 파일·코드 보고는 무의미하다** — 독자의 위치에서 요약하라. 확인이 필요한 것은 PR로
  옮기고 포커스만 짚는다. (교정 2026-07-08)
- 자기평가 수사 금지("완벽한·성공적·인상적") — 판정은 독자 몫, 나는 증거만. (Orwell "Politics
  and the English Language": 죽은 수사가 사고를 가린다 / Strunk & White: "Omit needless words")
- **사실 / 판단 / 추정을 문장 단위로 구분**해 표기한다. 섞이면 전부 추정 취급당한다.
- Grice 4격률로 자기검열: 필요한 만큼만(양)·참인 것만(질)·그 질문에(관련)·명료하게(태도).
- 표는 비교가 본질일 때만. 인과·서사가 본질이면 문장으로. (Bezos: 산문이 사고를 강제한다)

## 4. "검증됨"의 최소 조건

**실 환경(실 봇·실 파이프라인) 실행 + 관측된 결과 + 독자가 열람 가능한 위치.** 셋 중 하나라도
없으면 '검증'이라 쓰지 마라. 테스트 통과만 있으면 **"대본 검증"**이라고 따로 불러라 — 둘은
다른 단어다. (Popper: 검증은 통과가 아니라 반증 시도의 생존 / 교정: "실제 돌려본 결과가
있어야지") 검증 실행이 비결정적이면 어느 갈래가 관측됐고 어느 갈래가 미관측인지 명시한다.

## 5. 금지 목록 — 실제 교정에서 (재발 = 회귀)

| 실패 유형 | 교정 원문(2026-07-04~05) |
|---|---|
| 활동 나열(과정 보고) | "그냥 딸깍 하지만 말고 — 뭘/왜/결과/의미를 정리해서 보고하라" |
| 미완을 '한계'로 포장 | "자기선택은 당연히 LLM 지능이 관여해야 되고" |
| 실행 없는 '검증' 선언 | "구현 후 검증하라고 했잖아 — 실제 돌려본 결과가 있어야지" |
| 요구를 축소해놓고 완료 선언 | "내 Task를 제대로 이해한 게 맞아? 뭐가 최선인지 먼저 생각해" |
| 고도 오류(구현 디테일 보고) | "내가 구현해? 그럼 내가 구현 디테일 알아야 해?" |
| 결과물 미제시(말로 때움) | "실제로 돌리고 — 그 토론 결과 볼 수 있게 하라고" |
| 결정 요구 후치 | (라이브 전환 승인을 자발 보고하지 않고 추궁 후 제출) |
| 가독성 붕괴(밀도 벽문단·겹문장) | "다음에도 이따구로 보고하면 안됨. 가독성부터 해서 걍 구림"(2026-07-08, 온보딩 총정리 보고) |
| PR 포커스를 본문 글로만 설명(인라인 핀 0개) | "검수하라고 올려논 PR에 전혀 포커스 되지 않음 — 넌 나한테 읽을곳을 서빙해야지"(2026-07-08). §1-1의 인라인 코멘트는 **실제 diff 줄에 박는 것**이다 |
| 쪼금쪼금 작업 + 매 단위 검수 요청 | "개 쪼금쪼금 일하고 나한테 매번 검수 받으면 언제 일함?"(2026-07-08). 지시 단위 전체를 배치로 일하고 **묶어서 한 번에** 보고 |
| 결정 무지성 상신(맥락 없이·스스로 답 안 내보고) | "그게 뭔데 맥락 전혀 없잖아 … 답을 내리면 안되는거라 가져온거야, 아님 무지성으로 가져온거야"(2026-07-08) |

## 6. 발송 전 점검 5문 (5초)

1. 첫 줄이 **원 요구**의 상태인가?
2. 구현자가 아닌 독자가 전문을 이해하는가?
3. 모든 주장 옆에 증거(원자료)가 있는가?
4. 이 결과를 무효화할 수 있는 것을 적었는가?
5. 독자가 내릴 결정(또는 "없음")이 명시돼 있는가?

## 7. 전거 (2026-07-05 웹 검증 완료 — 서지·원문 인용·1차 출처)

- **Minto, *The Pyramid Principle: Logic in Writing and Thinking*(1985, 개정 1996).** McKinsey 최초
  여성 MBA 컨설턴트가 정립 — 지배적 결론 선행, 근거는 MECE 그룹. https://www.barbaraminto.com/
- **BLUF — 미 육군 AR 25-50 *Preparing and Managing Correspondence*.** "Army correspondence is
  action-oriented; it lets the reader know the purpose ... in the first sentence or paragraph."
  'bottom line up front' 문구는 2001 개정판에서 명문화.
  https://armypubs.army.mil/epubs/DR_pubs/DR_a/ARN42124-AR_25-50-007-WEB-13.pdf
- **Feynman, "Cargo Cult Science"(Caltech 졸업연설 1974, *Engineering and Science* 게재).** 원문:
  "report everything that you think might make it invalid — not only what you think is right
  about it" / "bending over backwards to show how you're maybe wrong."
  https://calteches.library.caltech.edu/51/2/CargoCult.htm
- **Orwell, "Politics and the English Language"(*Horizon*, 1946년 4월호).** 6규칙 — 특히
  "If it is possible to cut a word out, always cut it out."
  https://en.wikipedia.org/wiki/Politics_and_the_English_Language
- **Strunk & White, *The Elements of Style*(Strunk 원판 1918).** Rule 17 "Omit needless words" —
  "Vigorous writing is concise. A sentence should contain no unnecessary words ... but that he
  make every word tell." https://en.wikipedia.org/wiki/The_Elements_of_Style
- **Tufte, "The Cognitive Style of PowerPoint"(2003 팸플릿; *Beautiful Evidence*(2006) pp.156-185).**
  불릿 개요가 사고를 희석하고 증거 제시 품질을 떨어뜨린다(NASA 컬럼비아 사고조사 사례 분석).
  https://www.edwardtufte.com/book/the-cognitive-style-of-powerpoint-pitching-out-corrupts-within-ebook/
- **SBAR — Leonard M, Graham S, Bonacum D(2004), "The human factor: the critical importance of
  effective teamwork and communication in providing safe care", *Quality & Safety in Health Care*
  13(suppl 1).** 미 해군 전달 기법을 Kaiser Permanente가 의료 인수인계에 이식 — 상황-배경-평가-**권고**.
  https://www.ihi.org/library/tools/sbar-tool-situation-background-assessment-recommendation
- **Toulmin, *The Uses of Argument*(1958).** 주장(claim)-자료(data)-논거(warrant): 주장은 근거와
  물리적으로 붙어 다닌다. https://owl.purdue.edu/owl/general_writing/academic_writing/historical_perspectives_on_argumentation/toulmin_argument.html
- **Popper, *Logik der Forschung*(1934) / *The Logic of Scientific Discovery*(1959).** 어떤 수의
  실험도 이론을 증명하지 못하고, 재현 가능한 관측 하나가 반증한다 — 반증 시도의 생존만이 검증.
  https://plato.stanford.edu/entries/popper/
- **Grice, "Logic and Conversation", *Syntax and Semantics* vol.3: Speech Acts(1975), pp.41-58.**
  양(필요한 만큼만)·질(참·근거 있는 것만)·관련·태도(명료·간결·순서) 4범주.
  https://philpapers.org/rec/GRIISA
- **Bezos, Amazon 2017 주주서한.** 원문: "We don't do PowerPoint (or any other slide-oriented)
  presentations at Amazon. Instead, we write narratively structured six-page memos." — 산문이
  사고를 강제한다. https://www.aboutamazon.com/news/company-news/2017-letter-to-shareholders
