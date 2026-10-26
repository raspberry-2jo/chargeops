# 단일 Jenkins Job의 PR 승인 기반 Dev / Ops 배포

기존 chargeops-ci Job 하나를 사용한다. 앱 저장소의 kim_chanju 브랜치를 빌드하며 자동 환경 배포 대상은 CSMS다.

```text
chargeops/kim_chanju Push → CI·이미지 Push
→ GitOps main의 Dev digest·검사 기준 시각 기록
→ Argo CD Dev 자동 Sync → Dev RollingUpdate·OCPP 통합 검증
→ 앱 PR kim_chanju → dev 생성 → 같은 Jenkins 빌드에서 대기
→ 다른 사람의 최신 커밋 Approve + Merge
→ PR·승인·Merge 결과의 apps 트리·Dev 실행 digest 재확인
→ Dev에서 검증한 동일 digest를 GitOps main의 Ops 경로에 기록
→ Jenkins가 Ops 선택 Sync → Ops Canary → 완료
```

Approve만으로는 진행하지 않는다. Merge까지 완료해야 하며 최대 45분 기다린다.
45분 내 승인·Merge가 완료되지 않으면 해당 빌드의 열린 PR을 자동으로 닫고 실패 처리한다.
닫기 직전에 PR을 다시 조회하여 source SHA·kim_chanju→dev·저장소·빌드 소유 표시를 확인한다.
이미 Merge/Close됐거나 새 커밋·다른 빌드 소유 표시가 있으면 닫지 않는다.
GitHub API 오류로 닫기에 실패해도 Ops는 승격하지 않으며 콘솔에 확인 메시지를 남긴다.
이 동작은 새 코드로 시작한 빌드의 승인 대기 시간 초과에만 적용하며, 다른 실패나 수동 취소에는 적용하지 않는다.
대기 중 빌드는 실행 상태이고 mgmt-agent executor를 점유한다. 승인·Merge는 사람이 수행한다.
별도 dev 브랜치 배포 Job이나 Jenkins input 승인은 필요 없다. 기존 chargeops-ops-ci 또는 chargeops-ci-dev는 중지한다.
새 Jenkinsfile로 dev 브랜치를 실행하면 CI만 수행하며 이미지 게시·환경 배포는 하지 않는다.

앱 dev 브랜치는 승인된 소스가 합쳐지는 곳이다. Kubernetes namespace dev와는 별개다.
Argo CD는 앱 브랜치가 아니라 chargeops-gitops/main의 환경별 경로를 읽는다.
같은 브랜치 kim_chanju → kim_chanju PR은 만들 수 없다.

## 승인과 동일 이미지 검증

PR 최신 head를 작성자 외 실제 사용자이며 write/maintain/admin 권한을 가진 사람이 승인해야 한다.
직접 Push, 봇·자기 승인, 변경 전 승인, 수정 요청, 변경된 PR head는 차단한다.
GitHub API는 merge_commit_sha 계약을 유지하는 2022-11-28 버전으로 고정했다.
Merge 커밋은 현재 앱 dev HEAD여야 하며, Merge 결과의 apps 디렉터리 Git 트리는
빌드한 kim_chanju 커밋과 같아야 한다. 여섯 Docker 빌드 컨텍스트가 apps 아래이므로
다른 dev 변경과 합쳐지며 앱 내용이 달라졌으면 기존 이미지를 승격하지 않고 재빌드·재검증한다.
Merge 후 이미지를 다시 만들지 않는다. Dev 검증 receipt, 실제 실행 이미지와 성공한 검증 Job UID,
GitOps 정책 fingerprint, PR 승인을 승격 직전 다시 확인한다. 검증 receipt는 2시간 유효하다.

## Jenkins 설정과 적용

1. chargeops-ci: 저장소 chargeops, Branch Specifier `*/kim_chanju`, Script Path `Jenkinsfile`.
2. 기존 별도 chargeops-ops-ci / chargeops-ci-dev를 Disable한다.
3. ENABLE_ENVIRONMENT_CD=true, ENABLE_CANARY=true, DEV_DELIVERY_MODE=auto.
4. DEV_ARGO_APPLICATION=chargeops-dev, OPS_ARGO_APPLICATION=chargeops-chatops-dev.
5. 새 CSMS 이미지를 확실히 검사할 최초 빌드는 BUILD_ALL_IMAGES=true로 실행한다.
6. Dev 검증 후 PR을 다른 사람이 승인·Merge한다. 같은 빌드의 대기 단계가 끝나고 Ops로 이어지는지 확인한다.

GitHub dev 보호 규칙의 필수 소스 체크는 chargeops/ci다. PR 준비 단계에서 CI 성공을 먼저 기록하여
Merge가 최종 Ops 배포 결과를 기다리는 순환 대기를 막는다. chargeops/dev-integration은 CSMS 변경 시 추가 체크다.
CSMS 미변경 빌드는 CI 후 소스 PR만 만들고 CSMS 배포·승격은 하지 않는다.

## 환경과 Canary

| 환경 | Argo Application | GitOps 경로 | 반영 방식 |
|---|---|---|---|
| dev | chargeops-dev | chargeops-chatops/overlays/development-rolling | 자동 Sync |
| ops | chargeops-chatops-dev | chargeops-chatops/overlays/canary (prod 포함) | 승인 후 Jenkins 선택 Sync |

목표 구성은 Dev RollingUpdate(2개) + Ops Canary(4개)다. Dev는 배포 후 Health/DB·OCPP Job,
Ops는 후보 실통신·시험 세션 정합성과 기존 5단계 지표 gate를 사용한다.
기존 Dev Canary에서 새 경로로 바꿀 때는 GitOps docs/dev-rolling-ops-websocket.md의 전환 도구를 먼저 사용한다.
현재 Application을 자동으로 새 경로로 바꾸지는 않는다. Ops 전용 fixture가 없으면 운영 승격을 막는다.
HTTP p95는 참고 지표이며 실패 조건에서 제외했다. 세부 기준은 GitOps docs/canary-five-level-gates.md를 따른다.
Jenkins는 Ops promote 직전 고정 기준 시각을 기록한다. 기존 Dev Canary 호환 모드에서만 Dev 기준 시각도 기록한다.
이미지 파일과 검증된 기준 시각 필드만 배포 커밋에 허용하며 정책·다른 환경 수정은 차단한다.
Ops Sync는 CSMS Deployment와 검사 설정·RBAC·수집 리소스만 반영한다. 다른 앱·PVC는 제외한다.
Ops WorkloadRef 구조에서 원본 Deployment replicas=0은 정상이고 실제 Pod는 Rollout이 관리한다. 새 Dev는 Deployment가 2개 Pod를 관리한다.

실패하면 기존 Abort·이전 Git digest 복원·안정 버전 확인 경로를 사용한다.
미완료 복구 receipt는 지우지 않는다. 더 새로운 독립 배포가 있으면 덮어쓰지 않는다.
PUBLISH_DEV_TAG=false를 유지한다. 같은 이미지 필드를 Image Updater와 동시에 관리하지 않는다.
RUN_OCPP_FIXTURE_TEST 기본값 false는 별도 DB 쓰기 통합 검사 설정이다.
Dev AnalysisTemplate의 메모리 전용 OCPP fixture 및 자동 dev-vcharger와는 별개다.

## 최종 확인

Jenkins artifacts의 candidate.json, verification.json, pull-request.json, source-approval.json,
production-delivery.json으로 커밋·PR·검증 digest·복구 결과를 추적한다.
Dev/Ops Deployment의 `.spec.template.spec.containers` 이미지가 승인 digest와 같은지,
Rollout이 Healthy/Completed인지, 해당 배포의 AnalysisRun이 Successful인지 함께 확인한다.
대시보드 초록 카드만으로 배포 완료를 판단하지 않는다. 선택 시간의 과거 검사도 표시된다.
로컬 테스트 통과는 실제 Jenkins·Kubernetes 배포 성공을 의미하지 않는다.

새 대시보드는 Dev·Ops를 선택하며 최근 검사 Job의 앱·DB·실통신 결과를 먼저 표시한다. Ops 전용 지표 gate는 상세 영역에 분리했다.
