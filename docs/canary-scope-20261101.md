# Canary 적용 범위 — CSMS만 (2026-11-01)

앞선 빌드 대상 판정 커밋 `c2a07ec`은 유지하고, 이 문서와 Canary 범위 변경은
별도 두 번째 커밋으로 관리한다. Trivy/pytest/통합 검사 항목이나 CSMS AnalysisTemplate
판정 기준은 수정하지 않는다.

## 수정 전 확인 결과

### 새 CSMS revision이 없을 때 canary_cd.py run

`run → deliver → sync → wait`는 새 ReplicaSet 또는 새 AnalysisRun의 생성을
별도 조건으로 요구하지 않았다. **이번 revision/operation marker의 Sync 성공**,
CSMS의 요청 Digest·generation·소유 ReplicaSet·Healthy 상태, 다른 승인 앱들의
완료 조건이 충족되면 같은 Healthy CSMS Digest로 새 ReplicaSet 없이도 통과할 수
있다. Sync 이후 조건이 이미 충족됐다면 첫 관측에서 통과한다.

따라서 "새 revision이 없으면 항상 즉시 통과"도, "항상 timeout"도 아니다.
조건을 만족하지 못하면 실패하거나 기본 wait timeout 1200초까지 대기한다.
Jenkins의 외부 Stage timeout은 30분이다. 과거 Sync의 성공 기록은 operation marker와
revision 확인 때문에 이번 실행의 성공으로 재사용하지 않는다.

이번 변경은 기존 wait 성공·실패 조건을 그대로 두고 **진입 범위를 빌드 대상에
따라 제한**한다. 같은 Healthy Digest의 즉시 통과 가능성도 단위 테스트로 재현했다.

### env_cd.py --canary의 Dev / Ops 의미

| 명령 | 기존 플래그 효과 |
|---|---|
| prepare --canary | Ops canary overlay의 fingerprint를 후보에 기록해 이후 정책 변경을 막음 |
| prepare의 Dev 경로 | DEV_GITOPS_OVERLAY가 결정. 플래그가 development-rolling을 Rollout으로 바꾸지 않음 |
| Dev baseline timestamp | development-canary 경로의 준비 과정에서 처리. development-rolling에는 이 플래그로 baseline을 추가하지 않음 |
| promote --canary | Ops canary fingerprint 재검증, Ops 검사 baseline timestamp 갱신, canary=true·Canary fingerprint를 delivery에 기록 |
| 실제 Sync·Rollout 관측 | env_cd.py 플래그 자체가 수행하지 않음. 이후 canary_cd.py run이 수행 |
| 실패 시 자동 복구 | canary=true receipt가 Canary runner의 복구 경로를 허용 |

## 수정 후 분기

| BUILD_IMAGES | prepare/promote --canary | Ops 실행 |
|---|---|---|
| chargeops-csms 포함 | ENABLE_CANARY=true일 때만 전달 | 기존 Ops Canary and automatic recovery |
| 다른 앱만 포함 | 전달하지 않음 | Ops regular deployment verification |
| 0개 | 전달하지 않음 | Ops promote·Canary·regular delivery 모두 생략. Dev는 기존 Digest로 통합 검사 |

`RUN_CSMS_CANARY = ENABLE_CANARY && selected.contains('chargeops-csms')`를
한 번 계산해 prepare·fixture 검사·preflight·promote·Canary 실행·post 복구에
같은 조건을 사용한다. CSMS가 선택됐는데 ENABLE_CANARY=false이면 기존 방침대로
Ops 승격을 거부하며 일반 배포로 우회하지 않는다.

Python 계층도 built_images를 재확인한다. Non-CSMS에 잘못 --canary가 넘어가더라도
prepare/promote는 Canary를 허용하지 않고 이유를 출력한다. Canary run/recover도
non-CSMS/0개 receipt를 읽으면 상태를 바꾸지 않고 종료한다. 기존 built_images가
없는 레거시 CSMS receipt는 호환성을 유지한다.

## 일반 Ops 배포

새 `scripts/ops_delivery.py`가 다음만 수행한다.

1. 승인 후보와 현재 GitOps production fingerprint, 수동 Ops Application의 경로·
   namespace·repo·revision·기존 실행 여부를 확인한다.
2. 승인된 변경 Deployment/CronJob만 Argo selective Sync한다. CSMS Deployment,
   Rollout, AnalysisTemplate, Canary 설정 리소스는 Sync 대상에 넣지 않는다.
3. 이번 Sync의 revision과 operation marker, Sync 성공을 확인한다.
4. Deployment는 정확한 컨테이너 Digest, observedGeneration, replicas/
   updated/ready/available 수가 원하는 수에 도달했는지 확인한다. replicas=0인
   virtual charger를 강제로 실행하지 않는다.
5. Collector는 실제 CronJob이므로 Sync 적용 기록과 jobTemplate Digest를 확인한다.
   과거 Job·Pod 이미지를 바꾸거나 즉시 수집 Job을 새로 실행하지 않는다.
6. 성공은 succeeded, 실패는 failed로 기록한다. CSMS abort·Git rollback·
   recovering 전환은 수행하지 않는다.

Ops CSMS Digest는 현재 Ops 값을 유지한다. Dev의 CSMS Digest는 통합 검사 anchor일
뿐이며 non-CSMS 승격 이미지 목록에는 넣지 않는다. Dev와 Ops CSMS Digest가 서로
다르더라도 Notifier-only 배포가 CSMS를 교체하지 않는 것을 검증했다.

## 0개 빌드와 상태 파일

Jenkins가 reset --preserve-delivery를 사용해 이전 production-delivery.json을
삭제하지 않는다. 이후 promote·Canary·regular stage는 실행하지 않는다. 현재
후보가 0개인데 CLI promote/run을 직접 호출해도 이전 delivery 파일을 덮어쓰지
않으며 canary=true 또는 phase=recovering을 새로 쓰지 않는다.

이전 성공 빌드의 delivery 기록에 canary=true가 남아 있는 것은 이 빌드가 새로
기록한 값이 아니다. 보존 이력이며 현재 0개 빌드의 receipt로 재사용하지 않는다.
이전 미완료 Canary 복구가 있으면 기존 reset 안전장치가 계속 빌드를 차단한다.
그 상태를 자동 삭제해 새 빌드를 성공시키지 않는다.

예시 로그:

```text
canary skipped: chargeops-csms unchanged
Ops promote skipped: no image changes; canary skipped: no image changes
Regular Ops completion: own_sync=True, phase=Succeeded, regular_workloads_and_digests_ready=True
```

## 검증

- CSMS-only: Canary 승인·run·wait 실행.
- Notifier-only: canary=false, CSMS/Analysis 리소스 없는 Sync, rollout·Digest 확인.
- Dev CSMS와 Ops CSMS가 다른 Notifier-only: Ops CSMS Digest 보존.
- 0개: reset/prepare/promote/run/recover에서 delivery 파일 bytes 보존.
- 일반 배포: stale operation, 잘못된 Digest, 이전 generation, 미가용 replicas,
  Sync Failed를 성공으로 처리하지 않음. 실패 상태는 failed이며 recovering 아님.
- 실제 Jenkins prepare shell에서 세 선택 경우에 --canary 전달 여부 확인.
- 기존 같은-Digest Healthy CSMS가 새 RS 없이 wait를 통과할 수 있음을 재현.

기존 Canary wait·preflight·fixture 검증·revision/operation 소유권·Rollout 상태
판정과 Dev 통합 검사 관련 함수 14개의 AST가 앞선 커밋과 동일함을 확인했다.
일반 배포를 허용하는 범위로 기존 "모든 Ops에 Canary 활성화 필수" 테스트만
"CSMS Ops에 필수"로 갱신하고, 기존 승인·검증 선후관계 검사는 유지했다.

실제 Jenkins 또는 Kubernetes 서버 배포는 이 PC에서 실행하지 않았다.

최종 검증: 전체 **411개 테스트 통과**(이전 392개 포함), Docker 입력·COPY 파일·
앱 Python 문법 검증 통과. Trivy 정책·CSMS release_probe.py는 변경하지 않았다.
