# Dev 통과 시간 계측 — 1단계 A/B (2026-11-01)

## 확인 결과와 범위

기준 커밋 `318b34f`에서 실제 코드를 확인했다.

| 항목 | 수정 전 | 수정 후 |
|---|---|---|
| env_cd.verify 폴링 sleep | 5초 하드코딩 | CD_POLL_SECONDS, 기본 5초 |
| canary_cd.wait (run 경로) 폴링 sleep | 5초 하드코딩 | 같은 환경변수, 기본 5초 |
| Dev 진행 로그 | 30초마다, 상태 변경 즉시 출력 없음 | 상태 변경 또는 마지막 출력 후 30초 |
| Ops completion 로그 | 조건 변경 또는 6회 폴링마다 | 조건/sync/health 변경 또는 마지막 출력 후 30초 |

따라서 #77의 32~39초 로그 간격은 sleep 30초의 증거가 아니다. 현재 코드에는
두 루프 모두 sleep 5초가 있었고, 로그가 억제돼 있었다. API 호출·처리 시간도
반복에 더해진다. 현재 기본값의 실제 polling sleep은 짧아지지 않았으므로,
**이 변경만으로 완료 감지 시간이 30초→5초로 단축됐다고 주장하지 않는다.**
207/240초 측정의 양자화 원인과 적용 후 절감 시간은 새 시각 로그로 다시 측정한다.

상태 변경을 확인하기 위해 Dev 루프에서 별도의 Application 진단 조회를 수행한다.
진단 조회 오류는 Unknown으로 기록하고, 기존 배포 판정 자체의 API 오류는 그대로
처리한다. 유효한 상태가 확인되지 않았다는 이유로 성공을 만들어 내지 않는다.

작업 A는 `scripts/env_cd.py`, `scripts/canary_cd.py`,
`tests/test_cd_polling.py`를 별도 커밋으로 분리했다.
작업 B는 Dev 시각 로깅과 `tests/test_cd_timing.py`, 이 문서다.

## 환경변수

Jenkins Agent가 실행하는 Python 프로세스 환경에 설정한다. 지정하지 않으면 5초다.
Jenkinsfile은 변경하지 않았다. 값은 초 단위의 유한한 양수이며 소수도 허용한다.
0·음수·NaN·Infinity·문자열은 명확한 오류로 거부한다.

```bash
export CD_POLL_SECONDS=5
# 기존 Jenkins prepare → verify / Canary run 명령을 그대로 사용한다.
```

예를 들어 2초로 조정하려면 `CD_POLL_SECONDS=2`로 지정한다. 로그의 정기 출력
간격은 이 값과 독립적으로 30초다. API 호출이 오래 걸리면 실제 관측 간격은
sleep보다 길어진다. Dev 복구·Ops 복구의 다른 대기 루프는 별도로 변경하지 않았다.

## Dev 시각 로그

UTC ISO 8601(`+00:00`)을 사용하므로 Jenkins 화면 시간대와 구분한다.
`elapsed`는 verify 함수 진입 이후 monotonic 경과 시간이다.

| event | 기록 의미 |
|---|---|
| git_commit_completed | prepare에서 Dev Git commit/push 함수가 반환된 직후 시각을 후보 JSON에 보존. verify에서도 해당 시각을 다시 출력 |
| argo_detected | OutOfSync 관측, revision 변경 또는 후보 revision 일치. signal에 어떤 증거인지 표시 |
| synced | 후보 revision을 관측했거나 기존 exact-candidate 준비 검사가 통과한 상태에서 Synced 관측 |
| healthy | 같은 후보 바인딩과 Synced 조건 아래 Healthy 관측 |
| job_created | 기존 kubectl create가 성공한 시점 |
| job_completed | 기존 kubectl wait가 성공했거나, 대기 실패 후 Job Failed 조건이 확인된 시점 |
| job_wait_ended | wait 오류/timeout이지만 실제 Job 종료는 확인하지 못한 경우. 완료로 표시하지 않음 |

prepare는 verify보다 먼저 실행되므로 그때의 elapsed는 NA다. verify에서 재출력하는
commit 이벤트는 verify 기준 음수 경과 시간이 될 수 있다. 오래된 후보 JSON에
시각이 없으면 timestamp=unavailable, elapsed=NA로 기록하고 기존 후보를 거부하지
않는다. Git 시각의 상대값만 wall-clock 차이이며 나머지 경과 시간은 monotonic이다.

아래는 **단위 테스트의 가상 시계 예시**다. 실제 Jenkins 성능 측정값이 아니다.

```text
Dev timing: at=2026-11-01T00:00:05.000+00:00 elapsed=5.000s event=argo_detected signal=candidate_revision
Dev timing: at=2026-11-01T00:00:10.000+00:00 elapsed=10.000s event=synced source=candidate_revision
Dev timing: at=2026-11-01T00:00:20.000+00:00 elapsed=20.000s event=healthy source=candidate_revision
Dev timing: at=2026-11-01T00:00:20.000+00:00 elapsed=20.000s event=job_created job=check
Dev timing: at=2026-11-01T00:00:27.000+00:00 elapsed=27.000s event=job_completed job=check outcome=success
Dev timing summary: at=2026-11-01T00:00:27.000+00:00 detect=5.000s sync=5.000s healthy=10.000s job=7.000s total=27.000s outcome=success
```

summary는 구간 소요 시간이다: detect=verify→Argo 감지, sync=감지→Synced,
healthy=Synced→Healthy, job=생성→완료/대기 종료, total=verify 진입→반환/실패.
수집하지 못한 구간은 NA이며 0으로 위장하지 않는다. total에는 사전 검증과 최종
구성 재확인·receipt 저장도 포함돼 구간 합계와 정확히 같지 않을 수 있다.

시각 로그는 관측이다. API를 폴링해 확인한 시각이며 서버 내부 전환의 정확한
발생 시각은 아니다. `argo_detected signal=out_of_sync`만으로 해당 후보가 준비됐다고
판정하지 않는다. 기존 exact image·generation·replica·소유권·최종 fingerprint
검사는 그대로 유지한다. 최초의 이전 Synced/Healthy 상태는 후보 milestone도,
성공 판정도 만들지 않는다.

## 유지한 것

- Dev 기본 수렴 예산 600초, 기존 Dev Canary 경로의 900초 예외와 명시적 timeout 옵션.
- Jenkins Stage timeout 25분/30분, Stage 순서·병렬 구조.
- 통합 검사 Job 내용·activeDeadlineSeconds·kubectl wait 270초·성공 조건.
- Canary 단계·AnalysisTemplate·Sync operation/revision 소유권 확인·복구 동작.
- pytest·Trivy 정책, 이미지 빌드·ECR·maxSurge·HPA.

Ops 코드의 변경은 요청한 wait 폴링 간격 설정과 로그 제한뿐이다.
실패 Job 진단은 원래 wait 예외를 그대로 다시 던지고 성공 receipt를 생성하지 않는다.

## 검증 및 다음 측정

수정 전 전체 tests는 요청에 적힌 227개보다 늘어난 355개였으며 모두 통과했다.
수정 후 전체 **372개 통과**, 새 단위 테스트 17개 통과,
`python scripts/ci.py validate` 통과를 확인했다. 추가로 기존 성공/실패·소유권·
통합 Job 정의·승격 관련 함수 13개의 AST를 기준 커밋과 비교해 변경 없음을 확인했다.
새 단위 테스트는 기본값/override/invalid 값, 두 루프의 간격, 상태 변경/30초 로그,
이전 상태 거부, 단계별 시각/요약, 실패 Job과 timeout 구분을 검증한다.
Jenkinsfile 및 GitOps YAML은 수정하지 않았다.

실제 Jenkins를 실행한 결과는 아직 없다. 같은 부하와 이미지 조건으로 반복해
detect/sync/healthy/job/total을 각각 비교한다. 특히 111초 공백이 Healthy 대기인지
Job 실행인지 구분하고, 5초 기본값에서 API 지연을 포함한 관측 간격을 확인한 뒤
다음 성능 개선을 결정한다.
