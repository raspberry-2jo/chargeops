# ChargeOps 앱을 infra/app으로 이전한 내역과 CI/CD 연결 절차

## 로컬 이전 범위

- 대상: `https://github.com/raspberry-2jo/chargeops.git`, 로컬 작업 브랜치 `kim_chanju`.
- 원본: `https://github.com/chanjook1m/chargeops.git`, `main` 커밋 `d7c728ae3f624d93d2a30cad1d4284b3a3145984`.
- 원본의 추적 파일 전체를 `infra/app/`에 git subtree로 가져왔습니다. 원본 커밋 이력도 로컬에 포함합니다.
- 기존 대상 저장소의 루트 `apps/`, `db/`, `infra/ansible/` 등은 유지하며 `origin/dev`의 팀 변경을 로컬에 병합했습니다.
- 원격 Push, PR 생성/병합, Jenkins Job 수정, 실제 배포는 수행하지 않습니다.

## Jenkinsfile 변경

사용할 파일은 루트가 아닌 `infra/app/Jenkinsfile`입니다. Jenkins의 Script Path를 바꿔도
checkout 위치는 저장소 루트이므로, 테스트와 Docker 빌드는 `dir('infra/app')`에서 실행합니다.

| 항목 | 이전 후 동작 |
|---|---|
| Checkout | 팀 저장소 전체 checkout |
| 변경 감지 | `infra/app/`만 Git diff로 조회하고 prefix를 제거하여 서비스별 변경 판정 |
| 앱 밖 변경 | 이미지 빌드와 앱 테스트 생략. 첫 실행/전체 빌드 요청은 예외 |
| 테스트 | `infra/app/requirements*.txt` 설치 후 `infra/app/tests/` 실행 |
| 레거시 이미지 3개 | Dockerfile `infra/app/docker/*`, 빌드 context `infra/app` |
| v2 이미지 6개 | Dockerfile `infra/app/apps/*`, 빌드 context `infra/app/apps` |
| 레지스트리 | 기존 `chanjookim/*`와 `dev`, `build-*` 태그 유지 |
| 이미지 Push | `PUBLISH_BRANCH = 'dev'`와 checkout 브랜치가 일치하고 PR 빌드가 아닐 때만 수행 |
| kim_chanju/PR 빌드 | 테스트와 로컬 Docker build만 수행. dev 태그 생성과 Docker Hub Push는 수행하지 않음 |

`BUILD_ALL_IMAGES=true` 또는 이전 성공 커밋을 찾을 수 없는 첫 빌드는 이미지 9개를 모두 빌드합니다.
Dockerfile은 원본처럼 상대 경로를 사용하므로 파일 안의 COPY 경로를 `infra/app/...`으로 바꾸지 않습니다.

## Jenkins 연결 (아직 수행하지 않은 서버 작업)

1. `kim_chanju` 변경을 리뷰하고 팀 절차에 따라 `dev`에 병합합니다. 여기서는 로컬 작업만 수행했습니다.
2. 기존 앱 Jenkins Job을 재사용하고 Git URL과 Script Path를 변경합니다.
   새 Job을 만들면 최초 빌드부터 전체 이미지를 빌드합니다.

| Jenkins 설정 | 값 |
|---|---|
| Definition | Pipeline script from SCM |
| SCM | Git |
| Repository URL | `https://github.com/raspberry-2jo/chargeops.git` |
| Credentials | 비공개 저장소면 해당 팀 저장소를 읽을 수 있는 GitHub credential |
| Branch Specifier | 병합 후 배포 CI는 `*/dev`; 작업 브랜치 검증은 `*/kim_chanju` |
| Script Path | `infra/app/Jenkinsfile` |
| Agent label | `chargeops-agent` |
| Docker credential ID | `dockerhub-write`, 사용자 `chanjookim`, Docker Hub 쓰기 토큰 |

3. Linux agent에 Git, Python 3.12/venv, Docker CLI/Engine을 준비합니다.
   Jenkins agent 실행 사용자에게 Docker Engine 접근 권한이 있어야 합니다. 앱 CI에는 kubectl이 필요하지 않습니다.
4. 기존 `chanjook1m/chargeops` Job이 같은 `dev` 이미지 태그를 계속 Push하지 않도록,
   새 Job 설정 완료 후 기존 SCM polling/webhook과 중복 Job을 정리합니다.
5. Build Now 또는 Build with Parameters로 첫 빌드를 실행하고 테스트/Build/Push를 확인합니다.
   첫 실행 후 Jenkinsfile의 5분 간격 SCM polling이 등록됩니다.
6. GitHub webhook을 쓰려면 새 저장소에 webhook을 등록하고 Jenkins Job에도 GitHub push trigger를 설정합니다.

기존 앱의 Slack `/deploy`는 Job 이름 `chargeops`를 호출합니다. 해당 기능을 계속 사용하려면
기존 Job 이름을 유지하세요. `BRANCH=main` 요청 파라미터는 이 Jenkinsfile에서 사용하지 않으며,
실제 빌드 브랜치는 위 SCM 설정에 따릅니다. `JENKINS_URL`, `JENKINS_USER`, `JENKINS_TOKEN`은 서버 환경이
달라질 때만 변경합니다. GitHub PAT와 Jenkins API token, Docker Hub token은 서로 다른 자격 증명입니다.

현재 설정은 dev 환경의 이미지를 dev 브랜치에서 발행하도록 구성했습니다.
향후 main에서 발행하려면 SCM 브랜치와 Jenkinsfile의 `PUBLISH_BRANCH`를 함께 변경합니다.
kim_chanju를 단순히 Push하는 것만으로는 이미지를 발행하거나 배포하지 않습니다.

## Argo CD / Image Updater 연결

배포 설정은 계속 `https://github.com/raspberry-2jo/chargeops-gitops.git`의 `main`에 있습니다.
앱 소스를 팀 저장소로 이동해도 Argo CD가 앱 소스의 `infra/app`을 직접 바라보도록 바꾸지 않습니다.
`infra/app`에는 Argo CD가 적용할 Kustomize overlay가 없습니다.

| 설정 | 유지할 값 |
|---|---|
| Application `repoURL` | `https://github.com/raspberry-2jo/chargeops-gitops.git` |
| Application `targetRevision` | `main` |
| Application `path` | `chargeops-chatops/overlays/dev` |
| ImageUpdater Git repository / branch | 같은 GitOps 저장소 / `main` |
| ImageUpdater Secret | `argocd/chargeops-git-write`; GitOps 저장소 Contents Read and write |
| ImageUpdater 추적 이미지 | 기존 Docker Hub `chanjookim/*:dev` 9개 |

흐름: **팀 앱 저장소 dev 변경 → Jenkins 테스트/이미지 Push → Image Updater가 GitOps main에 digest 커밋 → Argo CD Sync**.
Jenkins Git checkout 권한은 팀 **앱 저장소 읽기**, Image Updater PAT는 **GitOps 저장소 쓰기** 권한입니다.
앱 저장소를 이전했다는 이유만으로 Image Updater Secret을 다시 교체할 필요는 없습니다.

## 이전 후 확인

1. Jenkins console에서 새 저장소 checkout, `infra/app`에서 테스트 성공, `publish images=true`를 확인합니다.
2. Docker Hub에서 선택된 이미지의 `dev`와 `build-*` 태그를 확인합니다.
3. GitOps main의 새 digest 커밋과 Argo CD Synced/Healthy를 확인합니다.
4. 관리 VM에서 아래 상태를 확인합니다.

```bash
kubectl -n argocd get application chargeops-chatops-dev
kubectl -n argocd describe imageupdater chargeops-image-updater
kubectl -n chargeops get deployments,pods,pvc
kubectl -n chargeops rollout status deployment/ai-gateway --timeout=180s
kubectl -n chargeops port-forward service/ai-gateway 18000:8000
```

다른 터미널에서 `curl -fsS http://127.0.0.1:18000/cicd-healthz`를 확인합니다.
필요하면 앱의 `infra/app/apps/ai_gateway/health.py` marker를 한 번 변경하고 dev에 병합한 뒤
동일 경로로 새 marker 응답을 확인합니다. 원본 앱의 초기 marker는 `ci-cd-probe-v1`입니다.

기존 Progressing 문제는 이 저장소 이전만으로 해결되지 않습니다. Pod Events/Logs, Secret, DB/Slack,
readiness probe, PVC를 별도로 확인해야 하며 앱 CI 성공만으로 업무 서비스의 정상 동작을 보장하지 않습니다.
롤백 시 Jenkins SCM을 이전 저장소로 복원하고 필요하면 GitOps digest 커밋을 revert합니다.
Image Updater가 롤백 digest를 다시 덮어쓰지 않도록 해당 이미지 업데이트도 조정합니다.

## 검증 범위

로컬 검증 결과:

- 원본 추적 파일 45개 모두 이전했으며 Jenkinsfile과 이전 안내를 추가한 문서 외에는 원본 내용을 유지했습니다.
- Windows Python 3.13 가상환경에서 기존 앱 테스트 2개가 통과했습니다. Jenkins의 Python 3.12 실행은 아직 확인하지 않았습니다.
- Linux에서 Jenkinsfile 내부 shell 문법과 9개 Docker context/COPY 경로를 확인했습니다.
- Docker 명령을 기록하는 stub으로 이미지별 선택 빌드, 전체 9개 빌드, 선택 없음의 실행 경로를 확인했습니다.
- 실제 임시 Git 저장소에서 앱 변경/앱 밖 변경/첫 실행의 diff 명령 결과를 확인했습니다.

실제 Jenkins 실행, Docker 이미지 Build/Push, 클러스터 rollout은 서버 연결 후 검증해야 합니다.
Collector의 DB 통합 검증 및 Slack/DB/Ollama 업무 기능은 기존 앱 테스트 2개에 포함되지 않습니다.

공식 참고: [Jenkins dir 단계](https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/#dir-change-current-directory),
[Jenkinsfile 사용](https://www.jenkins.io/doc/book/pipeline/jenkinsfile/).
