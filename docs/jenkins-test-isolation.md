# Jenkins #54 테스트 실패 수정

`DEV_GITOPS_OVERLAY=chargeops-chatops/overlays/development-rolling`을 상속한 pytest가
레거시 테스트 fixture를 RollingUpdate 경로에 만들었다. 이후 Canary 테스트가 찾는
development 디렉터리가 없었고, prepare가 RollingUpdate 분기로 들어가 실제 클러스터를
조회하면서 Forbidden도 발생했다. 테스트 실패를 운영 RBAC 문제로 판단하면 안 된다.

tests/conftest.py에서 테스트 모듈을 수집하기 전에 배포 overlay/Application 환경변수를
제거한다. 실제 Jenkins 배포 프로세스의 환경변수는 변경하지 않는다.
개별 설정 시험은 monkeypatch로 명시적인 값을 지정한다.

동일 환경에서 검증:

```bash
DEV_GITOPS_OVERLAY=chargeops-chatops/overlays/development-rolling \
DEV_ARGO_APPLICATION=jenkins-custom-dev \
python -m pytest -q tests/
```

Ops 기본 정책은 기존 충전기 지표로 판정하므로 시험 ID가 없어도 PR 생성과
승인 후 동일 digest 승격을 진행할 수 있다. 선택 검사인 websocket-ocpp-smoke를
정책에 명시적으로 추가한 경우에만 전용 시험 ID 설정을 검사한다.
Canary의 최근 Heartbeat 표본과 OCPP 처리 결과는 별도 GitOps 정책에서 확인한다.
