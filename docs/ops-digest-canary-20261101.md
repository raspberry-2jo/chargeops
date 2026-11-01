# Ops 실제 Digest 변경에 따른 Canary 판단

## 원인

기존 Jenkins와 검사기는 `built_images`에 CSMS가 있는지로 Canary를 선택했다.
이 목록은 이번 빌드가 만든 이미지 목록이며, Dev에서 검증한 승격 대상과 Ops 현재값의
차이를 나타내지는 않는다. 이전 빌드가 Dev까지만 완료됐으면 두 목록은 달라질 수 있다.
이전 release 조합 생성도 미선택 CSMS에 Ops 값을 유지했으므로 함께 수정했다.

## 대상과 실제 변경 목록

prepare는 Ops 전체 이미지 baseline을 `previous_production_images`에 고정한다.
승격 대상 `release_images`는 Ops 조합을 바탕으로 Dev에 기록된 앱 Digest와 이번에 빌드한
Digest를 반영한다. CSMS 대상은 반드시 이번 Dev 통합 검사에 사용한 `candidate.image`다.
Dev에 없는 일반 앱은 기존 Ops Digest를 유지하며, 새로 빌드한 일반 앱은 기존 정책처럼
별도 이미지 검사 후 승격된다. CSMS-only Dev 검사가 다른 앱의 런타임 검사까지 했다는
의미는 아니다. 일반 앱 전체를 Dev에 배포하는 변경은 이번 작업에 포함하지 않는다.

Dev 검증 완료 후 `env_cd.py plan-ops`가 Ops 현재 조합과 고정 대상을 비교한다.
이때 영수증·기준 fingerprint를 확인하고, `artifacts/ops-plan.json`에 변경 이미지와
Canary 여부를 기록한다. source PR 승인 대기 중 Ops가 바뀌면 기존 fingerprint 검사로
승격을 차단하며, promote 시 계획을 다시 계산한다.

| 상황 | Ops 실행 |
|---|---|
| notifier만 빌드, 검증한 Dev CSMS와 Ops CSMS가 다름 | notifier와 CSMS 승격 + Canary |
| notifier만 빌드, CSMS Digest 동일 | notifier 일반 배포 검증, CSMS Canary 생략 |
| CSMS 빌드, 승격 CSMS Digest가 Ops와 다름 | Canary |
| CSMS를 빌드했지만 Digest까지 동일하고 다른 변경도 없음 | Ops promote 생략 |
| 새로 빌드한 이미지는 있으나 Ops 변경 이미지 0개 | Ops promote·Sync 생략, 빈 GitOps 커밋 없음 |
| 빌드 대상 자체가 0개인 CI-only 빌드 | 기존 정책대로 Dev 검증 전용, PR·Ops 승격 생략 |

같은 Digest이면 CSMS를 빌드했더라도 Canary를 반복하지 않는다. 실행 기준은
빌드 유무가 아니라 `Ops 현재 CSMS Digest != 승격 CSMS Digest`다.
CSMS 변경이 있는데 ENABLE_CANARY=false이면 일반 배포로 우회하지 않고 중단한다.

## 실행·복구

Jenkins는 `HAS_SELECTED_IMAGES`를 빌드용으로 유지하고 `HAS_OPS_CHANGES`를 별도로
사용한다. `RUN_CSMS_CANARY`는 `ops-plan.json`의 실제 비교 결과에서 설정한다.
Dev prepare에는 ENABLE_CANARY를 전달해 미선택 CSMS 변경에도 Ops Canary 정책
fingerprint가 포함되도록 한다. Dev overlay, 단계, 계측 기준은 변경하지 않는다.

production-delivery.json에는 빌드 목록과 별개로 `ops_images`와 `ops_changes`를 기록한다.
승격할 이미지 pin, 선택 Sync 대상, 일반 배포 검증과 Canary 실행·복구는 이 실제 변경
목록을 사용한다. CSMS를 빌드하지 않았다는 이유로 Abort·복구가 생략되지 않는다.
기존처럼 Canary 실패 복구는 CSMS를 이전 Digest로 되돌리고 일반 앱은 유지한다.

로그 예시:

```text
ops changes: chargeops-csms, chargeops-notifier
```

```text
ops changes: chargeops-notifier
canary skipped: ops csms digest unchanged sha256:...
```

```text
ops changes: (none)
Ops promote skipped: no Ops digest changes
```

## 검증 및 실험 커밋

실제 임시 Git/Kustomize 저장소에서 위의 분기, Digest pin, 승인 우회 차단,
미선택 CSMS의 Canary 실패와 복구를 재현한다. Kubernetes API와 Sync·wait는
결정적으로 대체하므로 실제 클러스터에서 배포 완료를 확인한 결과는 아니다.

수정 커밋을 먼저 push한 뒤 별도 실험 커밋에서 CSMS의 `# exp4-1`을 지우고,
notifier Python 소스 끝에 `# exp4-2`만 추가한다. 실험의 빌드 선택 목록은
이전 성공 커밋과의 diff에 따라 달라진다. 그 기준에 exp4-1이 들어 있으면 주석 삭제도
CSMS 변경으로 잡힌다. 실험 시 image-selection.json의 base와 selected를 함께 확인한다.

최종 검증: 전체 pytest **445 passed**, Docker 입력·앱 Python 문법 검사 통과.
실제 Jenkins/클러스터 실행 확인은 다음 빌드에서 수행해야 한다.
