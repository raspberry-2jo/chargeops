# 실제 폴링 간격과 조회 소요 시간 확인 — 2026-11-01

## #84 로그에서 확정할 수 있는 것

진행 로그의 약 30초 간격과 이벤트 로그의 시각 일치만으로 실제 조회가 30초마다
실행됐다고 단정할 수 없다. 현재 코드는 매 조회 결과에서 상태 변화를 확인하고,
상태가 바뀌면 Dev timing 이벤트와 Dev verification 진행 로그를 모두 즉시 출력한다.
같은 조회에서 두 줄이 함께 나오는 것은 정상이다. 정기 진행 로그는 원래 30초로
제한돼 있으므로 중간 조회는 출력되지 않을 수 있다.

확인한 현재 소스의 sleep 기본값은 5초다. 이전에도 기본 sleep은 5초였으며 앞선
변경은 환경변수 지원·상태 변경 즉시 로그·시각 계측 추가였다. 실제 #84 Agent의
환경변수와 조회 지연은 제공된 로그만으로 확인하지 못했다.

제공된 elapsed 값에도 65→92초처럼 27초 간격이 있다. 진행 로그 시각의 일치만으로
고정 30초 sleep을 입증하지는 못하며, 실제 호출 간격을 별도로 계측해야 한다.

## 추가한 로그

Dev verify, Ops Canary wait, Ops regular wait 각각 시작할 때 적용값을 출력한다.

```text
CD poll config: at=... scope=dev_verify sleep_seconds=5.000 source=default diagnostics=false log_heartbeat_seconds=30 entry_sha256=... observer_sha256=...
```

- sleep_seconds: 파싱 후 실제 time.sleep에 넘기는 초 값.
- source: CD_POLL_SECONDS가 지정돼 있으면 environment, 없으면 default.
- entry_sha256: 실행 진입 스크립트 파일의 SHA-256 앞 12자리.
- observer_sha256: 공통 env_cd.py 파일의 SHA-256 앞 12자리.
- scope: dev_verify / ops_canary / ops_regular.

각 상태 확인 회차도 계측한다. 기본 출력은 첫 회차·30초 요약·종료·예외 시점이다.
아래 숫자는 설명용 예시이며 Jenkins 실측값이 아니다.

```text
CD poll sample: at=... scope=dev_verify poll=7 window_polls=6 sleep_seconds=5.000 check_seconds=0.120 start_gap_seconds=5.120 api_calls=6 api_seconds=0.110 window_max_check_seconds=0.140 window_max_start_gap_seconds=5.140 terminal=false error=false
```

| 필드 | 의미 |
|---|---|
| poll | 실제 상태 확인 회차의 누적 번호 |
| window_polls | 직전 진단 출력 이후 수행한 회차 수. 30초 로그 사이의 조회가 생략됐음을 확인 가능 |
| check_seconds | 이번 회차의 전체 확인 시간. sleep 제외, 상태 검사·판정·기존 상태 로그 처리 포함 |
| start_gap_seconds | 이전 회차 시작부터 이번 시작까지 실제 간격. 첫 회차는 NA |
| api_calls | 이번 회차의 kubectl JSON 조회 호출 수. HTTP 패킷/요청 수를 뜻하지 않음 |
| api_seconds | 위 호출의 누적 wall-clock 시간. 프로세스 기동·인증·통신·JSON 처리 포함, 서버 순수 RTT가 아님 |
| window_max_check_seconds | 출력 사이에 있었던 가장 긴 확인 시간 |
| window_max_start_gap_seconds | 출력 사이에 있었던 가장 긴 실제 시작 간격 |

start_gap은 **이전 회차**의 확인 시간 + sleep + 기타 처리/스케줄링 지연의 합이다.
이번 check_seconds와 항상 더해서 맞아야 하는 값은 아니다. 요약의 최댓값으로
로그 사이에 숨어 있던 느린 회차도 확인한다.

## 원인 구분

| 관측 | 해석 |
|---|---|
| sleep=30, source=environment | Agent/Job 환경에 30초 값이 실제 적용됨 |
| sleep=5, check≈25, api≈25, start_gap≈30 | sleep보다 kubectl 조회 시간이 병목 |
| sleep=5, check≈0.1, start_gap≈5.1, 30초 요약에 window_polls≈6 | 실제 5초 수준 조회이며 30초는 출력 제한 |
| sleep=5, check도 짧지만 start_gap만 큼 | sleep 이후 CPU 스케줄링·로그 처리 등 다른 지연도 확인 필요 |
| 새 config/sample 로그가 전혀 없음 | 새 진단 코드가 실행됐는지 checkout commit·스크립트 경로부터 확인 |

API 조회에 걸리는 시간과 실제 시작 간격은 monotonic clock으로 측정한다.
ISO 시각은 UTC +00:00이므로 Jenkins UI 시간대와 구분한다. 다음 빌드에서 출력되는
실제 수치로 판정해야 하며, 이 패치만으로 #84의 원인을 확정했다고 말하지 않는다.

## 매 회차 상세 진단이 필요할 때

기존 Python 실행 환경에 `CD_POLL_DIAGNOSTICS=true`를 지정한다. 그러면 매 회차
직전에 CD poll begin, 직후에 CD poll sample이 출력된다. 기본값은 꺼짐이다.
기존 Jenkins credential 주입/단계 안에서 실행하는 명령에 환경변수를 더하는 예:

```bash
CD_POLL_DIAGNOSTICS=true .ci-venv/bin/python scripts/env_cd.py verify
```

위 명령은 기존 verify 자체를 실행하므로 통합 검사 Job도 실행한다. 순수 읽기 전용
명령이 아니다. 추가 실행 없이 다음 정상 빌드의 기본 요약 로그부터 확인해도 된다.
Agent에서 코드 식별값을 비교하려면 해당 checkout에서 다음 읽기 전용 명령을 쓴다.

```bash
sha256sum scripts/env_cd.py scripts/canary_cd.py scripts/ops_delivery.py
```

## 변경하지 않은 것과 검증

sleep 기본값 5초·환경변수 정책·전체 timeout·상태 검사 순서·성공/실패 조건·
통합 검사 Job·이미지 선택·Canary 대상 범위·Trivy 정책을 변경하지 않았다.
kubectl 명령이나 API 조회 횟수를 추가하지 않고 기존 조회에 시간을 잰다.
인자·응답·kubeconfig·토큰은 진단 로그에 출력하지 않는다.

Dev의 통합 Job 대기는 폴링 회차가 아니다. job_created와 job_completed 사이에는
기존 Job 대기가 계속되며 이 구간의 시간은 앞서 추가한 Dev timing summary의
job 항목으로 본다. 복구 전용 다른 대기 루프는 이번 계측 대상이 아니다.

새 테스트는 환경변수 30초 override 표시, 5+25=30초 간격 재현, 실제 kubectl 호출
집계, 30초 로그 제한 사이 조회 횟수, 매 회차 trace, 예외 원본 보존 및 Dev 루프
연결을 검증한다. 두 기존 가상 시계 테스트는 추가 관측용 clock 호출에도 deadline
이후 시각을 유지하도록 보완했고, 기존 검증 조건은 그대로 유지했다.

진단용 with/표시문만 제거해 AST를 비교했을 때 Dev/Canary/regular 세 루프의 검사·
반환·sleep·deadline 로직이 이전 커밋과 동일함을 확인했다.

최종 검증: 기존 411개를 포함한 전체 **416개 테스트 통과**, Docker 입력·앱 Python
문법 검증 통과. Jenkinsfile·이미지 선택·Trivy 정책·CSMS 통합 검사 소스는 변경하지
않았다. 실제 #84 Agent 설정/네트워크와 다음 Jenkins 빌드는 이 PC에서 실행하거나
확인하지 못했으므로 실측 원인 판정은 새 로그를 받은 뒤 수행한다.
