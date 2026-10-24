# PR 승인 및 Slack 연결: 최초 설정과 검증

채널: **C0CBS4DFPGW**. Credential ID는 Jenkinsfile 기본값이다. 다른 ID로 등록했다면 Job 파라미터에서 실제 ID를 지정한다. 토큰 값은 코드·명령행·Git에 넣지 않는다.

| Credential ID | Kind / 용도 |
|---|---|
| chargeops-github-pr-token | Secret text. 앱 저장소 chargeops: Contents Read, Pull requests Read/Write, Commit statuses Read/Write, Metadata Read |
| chargeops-cicd-slack-token | Secret text. Slack Bot Token, chat:write; 봇을 채널에 초대 |
| chargeops-gitops-write | 기존 Username with password. GitOps main Push 및 자동 복구 |
| chargeops-dev-kubeconfig | 기존 Secret file. dev 검증 권한 |
| chargeops-canary-kubeconfig | 기존 Secret file. ops 관찰·Abort·Argo 선택 Sync |

등록만으로 Jenkins Job과 GitHub 보호 규칙까지 자동 변경되지는 않는다. 아래 설정은 최초 한 번 적용한다.

## 1. mgmt: 기존 Argo Application 접근 권한 연결

GitOps 저장소에서 최신 main을 받는다. 작업 중인 변경이 있다면 먼저 확인하고 보존한다.

```bash
cd ~/chargeops-gitops
git pull --ff-only origin main
kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,NAMESPACE:.spec.destination.namespace,PATH:.spec.source.path
```

| Jenkins 파라미터 | 기본 Application 이름 | Namespace | 경로 |
|---|---|---|---|
| DEV_ARGO_APPLICATION | chargeops-chatops-dev | dev | chargeops-chatops/overlays/development |
| OPS_ARGO_APPLICATION | chargeops-chatops | ops | chargeops-chatops/overlays/canary |

실제 출력이 다르면 **기존 Application 이름**을 지정한다. Application을 새로 만들거나 이름을 바꿔 같은 리소스를 중복 관리하지 않는다.

관리자 kubectl 컨텍스트에서 실행한다. .ci-venv에는 PyYAML이 필요하다.

```bash
.ci-venv/bin/python scripts/configure-ci-app-access.py \
  --dev-application chargeops-chatops-dev \
  --ops-application chargeops-chatops
```

환경·경로·main 추적과 Ops Manual Sync를 확인한 후, argocd namespace의 기존 Jenkins **Role/RoleBinding 네 개만** 설정한다. Application·Pod·Secret은 변경하지 않는다. 기존 kubeconfig 토큰을 재사용하며 bootstrap-canary.py를 다시 실행할 필요는 없다. 검증 실패 시 실제 Application 매핑부터 확인한다.

## 2. kim_chanju Job: Dev 검증 및 PR 생성

기존 chargeops-ci 설정:

- Pipeline script from SCM, 앱 저장소 https://github.com/raspberry-2jo/chargeops.git
- Branch Specifier: ***/kim_chanju**, Script Path: **Jenkinsfile**, agent: mgmt-agent.
- 최초 Build with Parameters: **ENABLE_ENVIRONMENT_CD=true, ENABLE_CANARY=true, BUILD_ALL_IMAGES=true**.
- Credential ID와 실제 Application 이름 확인. 기존 Job에 저장된 boolean 값이 false면 명시적으로 true로 설정.

새 파라미터가 보이지 않으면 Job을 한 번 실행해 정의를 읽힌 뒤 설정한다. 이후 BUILD_ALL_IMAGES=false로 사용한다. SCM polling은 H/5 * * * * 이다.

CI와 CSMS Dev 검증 통과 후 kim_chanju → dev PR을 생성한다. 열린 PR은 재사용한다. **이 Job은 Ops를 변경하지 않는다.** CSMS 미변경 시 CI 확인 후 PR만 생성하며 Dev CSMS 검증 완료로 표시하지 않는다.

dev가 예전 infra/app 구조라면 첫 PR에 루트 apps 및 Jenkinsfile 전환도 포함될 수 있다. 전체 Diff를 리뷰한다. notifier 등 다른 앱은 빌드·Push만 자동화되며 운영 반영은 별도다.

## 3. GitHub dev 보호 규칙

앱 저장소 Settings → Rules → Rulesets 또는 Branches에서 dev에 적용한다. 관리자 권한이 필요하다.

- PR 필수, 작성자 외 최소 1명의 승인 요구.
- 새 Push 시 이전 승인 해제, 마지막 Push에 대한 다른 사람 승인 요구.
- Require status checks: **chargeops/ci**. 최초 실행이 상태를 등록한 뒤 목록에서 선택한다.
- 직접 Push와 규칙 우회 권한 제한. Jenkins 토큰에 승인·Merge 우회 권한을 주지 않는다.

chargeops/dev-integration은 CSMS 변경에만 등록하는 추가 상태다. 모든 PR에 필수로 걸면 CSMS 미변경 PR이 대기하므로 공통 필수 체크는 chargeops/ci를 사용한다. Jenkins도 최신 변경 승인과 승인자의 저장소 쓰기 권한을 검증한다. 자동 승인·Merge는 하지 않는다.

## 4. dev Merge 감지용 Jenkins Job

kim_chanju Job을 유지하고, New Item → Pipeline → Copy from chargeops-ci로 **chargeops-ci-dev**를 만든다.

- SCM 저장소 동일, Branch Specifier만 ***/dev**, Script Path **Jenkinsfile**.
- Credential ID와 Application 이름 동일.
- **ENABLE_ENVIRONMENT_CD=true, ENABLE_CANARY=true**. 최초만 BUILD_ALL_IMAGES=true.
- dev에 루트 Jenkinsfile이 들어오는 첫 PR Merge 이후 실행한다. 최초 수동 실행이 SCM polling과 파라미터를 초기화한다.

이후 승인·Merge된 dev 변경을 감지하고 Merge 코드를 빌드·Dev 재검증한 동일 Digest를 Ops로 승격한다. 승인 또는 검증 실패 시 Ops 승격을 진행하지 않는다. 시연·부하 시험 중에는 이 **dev 승격 Job을 일시 중지**하고 Merge를 보류한다.

## 5. 확인 순서

1. kim_chanju Push: CI started → Dev verification passed → Source PR ready → Pipeline succeeded 알림. Ops 이미지는 그대로여야 한다.
2. 다른 사람이 PR 최신 변경 승인 후 dev Merge: dev Job의 Validate reviewed dev merge 통과.
3. Dev 재검증 성공: Ops Canary started 알림 후 Canary 완료와 Pipeline succeeded 확인.
4. 기존 Canary 실패 시험은 부하가 없는 시간에 수행한다. Recovery completed 알림과 Jenkins의 “Recovery complete: previous Git digest restored and stable Rollout verified”를 확인한다. 실패 배포의 최종 빌드는 FAILURE로 남는 것이 정상이다.
5. Recovery failed면 새 승격을 중단하고 production-delivery.json과 복구 로그를 확인한다. receipt나 Deployment/PVC/Rollout을 지워서 재시도하지 않는다.

Slack 오류는 토큰 없이 경고만 남긴다. 알림이 없으면 Credential ID, 봇 채널 참여, chat:write, agent의 Slack API 연결을 확인한다. 로컬 테스트는 실제 PR 생성·Slack 전송·운영 Sync 성공을 대신하지 않는다.
