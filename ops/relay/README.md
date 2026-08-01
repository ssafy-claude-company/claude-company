# 내 LLM을 murmur에 붙이기

공인 주소도 인증서도 포트포워딩도 필요 없다. 커넥터가 **안에서 밖으로** 붙는다.

```
설정 → 실행 설정 → + 설정 추가 → 종류 "내 컴퓨터에서 돌리기" → 저장
→ "연결 키 받기" → 아래 한 줄을 내 컴퓨터에서 실행
```

## 1. OpenAI 호환이면 주소만 준다

요즘 로컬 LLM 서버는 대개 OpenAI 호환 API를 낸다. 그러면 `LLM_URL`만 바꾸면 된다.

```bash
# Ollama (기본값이라 안 줘도 된다)
MURMUR_TOKEN=... python3 murmur-connector.py

# llama.cpp (llama-server)
LLM_URL=http://127.0.0.1:8080/v1/chat/completions \
  MURMUR_TOKEN=... python3 murmur-connector.py

# vLLM
LLM_URL=http://127.0.0.1:8000/v1/chat/completions \
  MURMUR_TOKEN=... python3 murmur-connector.py

# LM Studio
LLM_URL=http://127.0.0.1:1234/v1/chat/completions \
  MURMUR_TOKEN=... python3 murmur-connector.py

# 키를 요구하는 서버면
LLM_KEY=... LLM_URL=... MURMUR_TOKEN=... python3 murmur-connector.py
```

## 2. OpenAI 호환이 아니면 명령을 준다

직접 만든 모델, 사내 API, 특이한 서버 — 무엇이든 **글을 받고 글을 뱉으면** 붙는다.
`MURMUR_CMD`에 실행할 명령을 주면 stdin으로 프롬프트가 들어가고 stdout이 답이 된다.

```bash
MURMUR_CMD="python3 my_llm.py" MURMUR_TOKEN=... python3 murmur-connector.py
```

`my_llm.py`는 이 정도면 된다.

```python
import sys
prompt = sys.stdin.read()
print(내_모델이_생성한_답(prompt))
```

모양을 맞추는 일은 커넥터가 한다. 응답자는 글만 알면 된다 — 그래서 응답자를 바꿔도
murmur 쪽은 아무것도 안 바뀐다.

오래 걸리는 모델이면 `MURMUR_CMD_TIMEOUT`(초, 기본 600)을 늘리고, 실행 설정의
**응답 대기**도 함께 늘린다. 둘 중 짧은 쪽이 먼저 끝난다.

## 3. 사람이 답해도 된다

```bash
MURMUR_HUMAN=1 MURMUR_TOKEN=... python3 murmur-connector.py
```

프롬프트를 보여 주고 타이핑을 기다린다. 이때는 실행 설정의 **응답 대기**를 분 단위로
넉넉히 잡는다 — 기본 180초는 사람에게 짧다.

## 무엇이 지켜지나

- 토큰은 환경변수로만 받는다. 명령줄에 두면 `ps`로 옆 사람에게 보인다.
- LLM 주소 기본값은 로컬이다. 바깥 주소를 넣는 것은 사용자 선택이지 기본이 아니다.
- 커넥터가 죽어도 그 요청은 사라지지 않는다. 마감이 지나면 되돌아가 다시 배달된다
  (세 번까지, 그 뒤엔 실패로 닫고 이유를 남긴다).
- 명령이 실패하면 그 오류가 그대로 올라온다. 왜 실패했는지 모르면 붙일 수가 없다.
