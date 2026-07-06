"""system 레포 자체 브레인(코어 Rule) unittest 스위트.

PJT pytest 스위트(tests/{test_baton,test_busy_escalate,test_permissions,test_protocol,
test_audit,test_config}.py + test_sys.py 순수부)를 **표준 unittest 문법으로 번역 포팅**한 것 —
배포 체크아웃 venv에는 pytest·서드파티 테스트 의존이 없으므로 표준 라이브러리만 쓴다.
라이브 러너는 이 디렉터리를 import하지 않는다(런타임 영향 0).

실행(러너 명령):
  cd /root/murmur-stack && PYTHONPATH=/root/murmur-stack \
  /root/murmur-stack/.venv/bin/python -m unittest discover -s system/tests -t /root/murmur-stack -v

수록:
  test_comm.py        — CommunicationManager(베턴 LIFO·check_request·redo·escalate·report_up_to·
                        restore_chain·delivered_work)·Engagement(전역 점유·자가치유)·comm 헬퍼·Flow 마감
  test_permissions.py — make_pre_tool_use_hook deny 경로(권한·샌드박스·협의/흡수/리스 게이트)
  test_protocol.py    — 구조화 메시지 format/parse 왕복·멀티라인 본문
  test_task_rules.py  — task 순수 판정기(_wants_real_data·_synthesizes_data·_perceptual_essential 등)
  test_misc.py        — deploy 판정(deploy_service_name·_deploy_infeasibility)·표현 유틸
                        (_speech_clip·_looks_transient)·audit(JSONL·redact)·config(_require)
"""
