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
| DEV_ARGO_APPLICATION | chargeops-dev | dev | chargeops-chatops/overlays/development |
| OPS_ARGO_APPLICATION | chargeops-chatops-dev | ops | chargeops-chatops/overlays/canary |

실제 출력이 다르면 **기존 Application 이름**을 지정한다. Application을 새로 만들거나 이름을 바꿔 같은 리소스를 중복 관리하지 않는다.

관리자 kubectl 컨텍스트에서 실행한다. .ci-venv에는 PyYAML이 필요하다.

```bash
.ci-venv/bin/python scripts/configure-ci-app-access.py \
  --dev-application chargeops-dev \
  --ops-application chargeops-chatops-dev
```

환경·경로·main 추적과 Ops Manual Sync를 확인한 후, argocd namespace의 기존 Jenkins **Role/RoleBinding 네 개만** 설정한다. Application·Pod·Secret은 변경하지 않는다. 기존 kubeconfig 토큰을 재사용하며 bootstrap-canary.py를 다시 실행할 필요는 없다. 검증 실패 시 실제 Application 매핑부터 확인한다.

## 2. kim_chanju Job: Dev 검증 및 PR 생성

기존 chargeops-ci 설정:

- Pipeline script from SCM, 앱 저장소 https://github.com/raspberry-2jo/chargeops.git
- Branch Specifier: ***/kim_chanju**, Script Path: **Jenkinsfile**, agent: mgmt-agent.
- 최초 Build with Parameters: **ENABLE_ENVIRONMENT_CD=true, ENABLE_CANARY=true, BUILD_ALL_IMAGES=true**.
- Credential ID와 실제 Application 이름 확인. 기존 Job에 저장된 boolean 값이 false면 명시적으로 true로 설정.

새 파라미터가 보이지 않으면 Job을 한 번 실행해 정의를 읽힌 뒤 설정한다. 이후 BUILD_ALL_IMAGES=false로 사용한다. SCM polling은 H/5 * * * * 이다.

CI와 CSMS Dev 검증 통과 후 kim_chanju → dev PR을 생성한다. 열린 PR은 재사용한다. **이 Job이 PR 승인·Merge를 최대 45분 기다린 뒤 검증한 동일 digest를 Ops로 승격한다.** CSMS 미변경 시 CI 확인 후 PR만 생성하며 Dev CSMS 검증 완료로 표시하지 않는다.

dev가 예전 infra/app 구조라면 첫 PR에 루트 apps 및 Jenkinsfile 전환도 포함될 수 있다. 전체 Diff를 리뷰한다. notifier 등 다른 앱은 빌드·Push만 자동화되며 운영 반영은 별도다.

## 3. GitHub dev 보호 규칙

앱 저장소 Settings → Rules → Rulesets 또는 Branches에서 dev에 적용한다. 관리자 권한이 필요하다.

- PR 필수, 작성자 외 최소 1명의 승인 요구.
- 새 Push 시 이전 승인 해제, 마지막 Push에 대한 다른 사람 승인 요구.
- Require status checks: **chargeops/ci**. 최초 실행이 상태를 등록한 뒤 목록에서 선택한다.
- 직접 Push와 규칙 우회 권한 제한. Jenkins 토큰에 승인·Merge 우회 권한을 주지 않는다.

chargeops/dev-integration은 CSMS 변경에만 등록하는 추가 상태다. 모든 PR에 필수로 걸면 CSMS 미변경 PR이 대기하므로 공통 필수 체크는 chargeops/ci를 사용한다. Jenkins도 최신 변경 승인과 승인자의 저장소 쓰기 권한을 검증한다. 자동 승인·Merge는 하지 않는다.

## 4. 단일 배포 Job 유지

별도 dev 브랜치 배포 Job은 만들지 않는다. 기존 chargeops-ops-ci / chargeops-ci-dev는 Disable한다.
chargeops-ci의 같은 실행이 Dev 검증 → PR 생성 → 승인·Merge 대기 → Ops 승격까지 수행한다.
대기 중 mgmt-agent executor는 점유된다. Approve만으로는 진행하지 않는다.
Merge 결과 apps 빌드 컨텍스트가 후보와 다르면 재빌드·재검증이 필요하다.

## 5. 확인 순서

1. kim_chanju 빌드의 Dev verification passed와 Source PR ready를 확인한다.
2. Wait for source PR approval and merge 단계에서 대기하며 Ops digest가 유지되는지 확인한다.
3. 다른 사람이 최신 head를 Approve하고 Merge한다.
4. 같은 Jenkins 실행이 승인 검증 → 동일 digest Ops 승격 → Canary까지 이어지는지 확인한다.
5. 실패하면 recovery receipt와 Git 복원·안정 Rollout 확인 로그를 확인한다. 미완료 receipt를 삭제하지 않는다.

PR 준비 시 chargeops/ci 성공을 먼저 기록한다. 최종 Ops 완료를 기다려야 Merge할 수 있는 순환 대기를 막는다.
전체 흐름과 환경 매핑은 [단일 Job 배포 안내](environment-delivery.md)를 따른다.

## 6. PR 승인·수정 요청·종료·Merge 알림 Job

리뷰·종료 알림은 선택적인 **관찰 전용 Job**이 담당한다. 배포 Job의 승인·Merge 확인과 별개다.
배포 Job이 대기하며 executor를 점유하므로, 알림 Job은 별도 executor가 있어야 동시에 실행된다. GitHub Actions Secret이나 새 토큰, Webhook 서버는 필요 없다. 기존 Jenkins Credential 두 개를 재사용한다.

Jenkins에서 최초 한 번 설정한다.

1. New Item → 이름 **chargeops-pr-notifications** → Pipeline → Copy from **chargeops-ci**.
2. Pipeline script from SCM 설정에서 저장소와 SCM 인증은 유지하고, Branch Specifier를 ***/kim_chanju**, Script Path를 **Jenkinsfile.pr-notifications**로 지정한다.
3. 저장 후 **Build Now**로 최초 한 번 실행한다. 새 파라미터가 적용되면 Credential ID가 `chargeops-github-pr-token`, `chargeops-cicd-slack-token`인지 확인한다. Slack 채널은 `C0CBS4DFPGW`이다.
4. 최초 실행이 성공하면 1분마다 PR 상태를 확인한다. agent 대기·네트워크 지연이 추가될 수 있다. 이 Job에서는 이미지 빌드, Dev/Ops 배포, PR 승인·Merge를 하지 않는다.

기존 Job 복사 시 상속된 **Build Triggers → Poll SCM** 설정은 해제한다. 알림 Job은 Jenkinsfile에 정의된 1분 주기의 Build periodically만 사용한다. 기본 작업공간을 사용하고 CI Job과 같은 custom workspace를 지정하지 않는다.

| GitHub에서 한 작업 | Slack 알림 | 배포 의미 |
|---|---|---|
| Review → Approve | PR 리뷰 승인 / 리뷰어 / PR 링크 | 아직 Merge 전이므로 이 이벤트만으로 Ops 배포하지 않음 |
| Review → Request changes | PR 수정 요청 / 리뷰어 / PR 링크 | 수정 요청. PR을 닫거나 배포하지 않음 |
| Close pull request (Merge 없음) | PR 거부·종료 / PR 링크 | Ops 승격 없음. Dev 자동 롤백 없음 |
| PR Merge | PR Merge 완료 / 처리자 / PR 링크 | 대기 중인 기존 빌드가 재검증 후 같은 digest를 Ops로 승격. 배포 완료 알림은 별도 |

대상은 앱 저장소의 **kim_chanju → dev** PR이다. 댓글, 이모지 반응, 다른 브랜치 PR에는 결정 알림을 보내지 않는다. 이전 커밋에 대한 리뷰는 최신 변경 승인과 구분해서 표시한다. 이 알림은 기존 Jenkins 운영 승인 검증을 대체하거나 완화하지 않는다.

테스트 순서:

1. 알림 Job을 먼저 한 번 성공시킨다. 처음 실행할 때는 최근 2분 이후 이벤트부터 관찰하며 과거 전체 리뷰를 일괄 전송하지 않는다.
2. CI Job에서 PR 생성 → 다른 사람이 Approve → Slack 승인 알림 확인.
3. Request changes → Slack 수정 요청 알림 확인.
4. Merge하지 않고 Close → Slack 거부·종료 알림 확인. Ops가 그대로인지 확인.
5. 실제 운영 승격 시험을 할 때만 별도 PR을 승인·Merge하고 Merge 알림 및 기존 Dev/Ops 배포 알림을 확인한다.

전송 상태는 알림 Job 작업공간의 `.pr-notifications/state.json`에 저장하고 각 빌드 artifact로 보관한다. 정상 반복 실행에서는 같은 이벤트를 다시 보내지 않는다. Slack 실패는 해당 이벤트를 기록하지 않아 다음 실행에서 재시도하며, 중단 중 발생한 종료 이벤트도 마지막 정상 확인 이후부터 다시 확인한다. **Workspace 정리 시 최신 state.json을 같은 경로로 복원**한다. 상태 파일을 지우면 최근 이벤트가 다시 전송될 수 있다. Slack 전송 직후 상태 저장 전에 프로세스가 중단되는 경우 재전송 가능성은 있다.

이 구현은 GitHub [PR 조회 API](https://docs.github.com/en/rest/pulls/pulls)와 [리뷰 조회 API](https://docs.github.com/en/rest/pulls/reviews)를 읽고, Jenkins [cron 트리거](https://www.jenkins.io/doc/book/pipeline/syntax/#triggers)로 반복 확인한다.

## 7. Dev verify가 즉시 실패하고 PR이 생성되지 않을 때

`Development digest committed; production unchanged` 이후 verify가 실패하면 Dev GitOps는 이미 갱신됐지만 통합 검증이 완료되지 않은 상태다. PR 생성은 생략하고 Ops 승격도 진행하지 않는다. Dev를 이전 버전으로 되돌렸다는 의미는 아니다.

이전 코드는 kubectl 오류 원문을 숨기면서 모두 RuntimeError로 표시했다. 수정된 코드는 Secret이나 응답 본문을 출력하지 않고 Forbidden, NotFound, Unauthorized, TLS, API connection 오류를 구분한다. 실패한 Dev 상태도 GitHub에 failure로 정리한다.

mgmt 관리자 컨텍스트에서 실제 Application 매핑과 Jenkins ServiceAccount 권한을 확인한다.

```bash
kubectl -n argocd get applications \
  -o custom-columns=NAME:.metadata.name,NAMESPACE:.spec.destination.namespace,PATH:.spec.source.path
kubectl auth can-i get applications.argoproj.io/chargeops-dev \
  -n argocd --as=system:serviceaccount:dev:jenkins-dev-verifier
```

실서버 확인 결과 chargeops-dev가 Dev이고, chargeops-chatops-dev는 이름과 달리 Ops다. Dev 계정으로 chargeops-chatops-dev 조회가 no인 것은 정상이다. Dev 권한을 Ops로 확대하지 않는다. Jenkins의 DEV_ARGO_APPLICATION=chargeops-dev, OPS_ARGO_APPLICATION=chargeops-chatops-dev로 설정한다. 기존 Job에 저장된 파라미터는 이전 값일 수 있으므로 Build with Parameters에서 두 값을 명시적으로 지정한다. 올바른 Dev Application인 chargeops-dev 조회도 no일 때만 1절의 configure-ci-app-access.py로 기존 RBAC를 연결한다. Unauthorized/TLS/연결 오류는 RBAC 변경으로 해결되지 않으므로 kubeconfig 인증 또는 접속을 확인한다. 이후 ENABLE_ENVIRONMENT_CD=true, ENABLE_CANARY=true, BUILD_ALL_IMAGES=true로 재실행한다. Secret 값을 출력하거나 Deployment를 삭제할 필요는 없다.
