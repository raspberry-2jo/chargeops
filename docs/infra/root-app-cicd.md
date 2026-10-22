# 루트 apps 기준 CI/CD

작업 브랜치와 이미지 발행 브랜치는 `kim_chanju`다. 루트 `Jenkinsfile`을 사용한다.

## 구조와 코드 기준

| 경로 | 역할 |
|---|---|
| `apps/ai-gateway`, `chatops-bot`, `csms`, `collector`, `notifier`, `virtual-charger` | 서비스 코드, Dockerfile, requirements |
| `apps/common` | AI gateway·v2 bot·notifier가 사용하는 DB·권한 정책·Slack 메시지 공통 코드 |
| `scripts/ci.py` | 이미지 목록, 변경 감지, 빌드 입력 검증, Docker Build/Push |
| `requirements-dev.txt`, `pytest.ini`, `tests` | 루트 CI 테스트 설정 |

기존 루트 CSMS의 다중 Pod 전달·drain·세션 기록 기능과 Collector의 팀 환경변수 호환 코드를 유지했다. `infra/app`의 오래된 코드로 덮어쓰지 않았다. CSMS의 `requests` import에 맞게 requirements도 보완했다.

루트의 `apps/legacy`는 제거했다. `apps/common`은 유지하며 AI gateway·bot·notifier의 Dockerfile은 이 폴더를 컨테이너 내부 `/app/common`으로 복사한다. 각 서비스의 기존 `common.*` import와 공통 코드 사용 방식은 유지한다.

호스트 디렉터리의 하이픈 이름은 기존 팀 경로를 유지한다. Dockerfile은 `ai-gateway`를 `/app/ai_gateway`로, `chatops-bot`을 `/app/chatops_bot`으로 복사하므로 컨테이너 내부 Python 모듈 이름은 바뀌지 않는다.

기존 `infra/app/`은 이전 Jenkins Job과의 호환·복귀용으로 유지한다. 새 Job은 이 경로를 빌드하지 않는다. 기존 Job 하나를 재사용하면 Script Path만 변경하면 된다. 별도 새 Job을 운영하는 경우에만 같은 이미지의 `dev` 태그를 중복 발행하지 않도록 이전 발행 Job의 polling/webhook을 중단한다.

## Jenkins 설정

| 항목 | 값 |
|---|---|
| Repository | `https://github.com/raspberry-2jo/chargeops.git` |
| SCM Branch Specifier | `*/kim_chanju` |
| Script Path | `Jenkinsfile` |
| Agent | `mgmt-agent` |
| 도구 | Git, Python 3.12/venv, Docker CLI/Engine |
| Registry | `registry.chargeops.lab:5000/chargeops` |
| 이미지 발행 | `kim_chanju` 일반 빌드만 허용. PR·다른 브랜치는 빌드만 수행 |

`PUBLISH_BRANCH`는 발행 조건이며 Job의 SCM 감시 브랜치를 바꾸지는 않는다. GUI의 Branch Specifier도 함께 설정한다. Docker 로그인과 Registry credential은 사용하지 않는다. 에이전트와 클러스터에서 DNS·TCP 5000·TLS/HTTP 설정이 준비되어 있어야 한다.

최초 실행, 이전 성공 커밋을 찾지 못한 경우, `BUILD_ALL_IMAGES=true`, 루트 Jenkinsfile 또는 CI 도구 변경은 6개 이미지를 모두 빌드한다. 이후 앱별 변경은 해당 이미지만, `apps/common` 변경은 소비하는 3개 이미지만 빌드한다. 문서·DB·Terraform 및 이전 `infra/app`만의 변경은 새 앱 빌드를 생략한다. 테스트만 변경하면 테스트만 실행한다.

서비스 6개의 빌드 컨텍스트는 `apps`다. 예:

```bash
docker build --pull -f apps/ai-gateway/Dockerfile \
  -t registry.chargeops.lab:5000/chargeops/chargeops-ai-gateway:build-test apps
```

루트 서비스 6개는 Python 베이스 이미지 `chargeops/python:3.13-slim`만 사용한다. 이 이미지를 Registry에 먼저 준비한다. Dockerfile은 서비스별 requirements를 사용하며 루트 requirements-dev는 CI 테스트용이다. 이전 `infra/app` Job을 계속 사용하는 경우의 3.12 베이스 요구 사항은 이번 루트 구성과 별개다.

발행 이미지는 `chargeops-ai-gateway`, `chargeops-v2-chatops-bot`, `chargeops-v2-csms`, `chargeops-v2-notifier`, `chargeops-v2-vcharger`, `chargeops-v2-collector` 6개다. 각 이미지에 고유 `build-<번호>-<커밋>` 태그와 가변 `dev` 태그를 발행한다. 전체 선택 이미지 빌드가 성공한 후에만 Push한다.

독립 Collector의 `Containerfile`과 `collector:0.1` 수동 빌드 경로는 기존 설치 문서와의 호환을 위해 유지한다. Jenkins는 같은 루트 코드로 `chargeops-v2-collector`를 빌드한다. 두 CronJob 일정이 같은 API 키로 중복 수집하지 않도록 GitOps에서 실제 사용할 한쪽만 활성화한다. `collector:0.1`의 `IfNotPresent` 캐시 때문에 내용 변경 시 버전 태그 또는 digest도 함께 변경해야 한다.

## Argo CD 연결과 전환

이 Jenkinsfile은 `kubectl apply`, `helm upgrade` 또는 Argo CD 강제 Sync를 실행하지 않는다. 기존 GitOps 구성의 흐름은 다음과 같다.

```text
kim_chanju Push → Jenkins 테스트·이미지 Build/Push
               → Image Updater가 :dev의 digest 변경 감지
               → GitOps main에 digest 커밋
               → Argo CD가 Git을 읽어 클러스터에 적용
```

읽기 전용으로 확인한 GitOps `main`(`e0f52b5`)에는 다음 설정이 이미 있다. 클러스터에 적용되어 있다는 뜻은 아니므로 GUI와 실제 리소스도 확인한다.

- GitOps: `https://github.com/raspberry-2jo/chargeops-gitops.git`
- Application: `chargeops-chatops-dev`, 경로 `chargeops-chatops/overlays/dev`, 대상 namespace `ops`
- Auto-Sync: 활성, `selfHeal: true`, `prune: false`
- Image Updater: `chargeops-image-updater`, 9개 `:dev` 이미지를 digest 전략으로 추적, Kustomization에 Git write-back

위 GitOps 확인 시점에는 레거시 3개도 포함되어 있다. 이번 루트 코드 삭제만으로 배포 리소스가 삭제되지는 않는다. GitOps의 레거시 bot·alert-router·mock-csms 리소스와 이미지 추적 항목은 별도로 정리해야 한다. 자동 prune이 꺼져 있으므로 클러스터의 기존 리소스 제거도 별도로 확인한다.

소스 폴더를 옮겨도 이미지 이름·컨테이너 모듈 이름을 유지하므로 GitOps의 소스 경로를 앱 저장소로 바꾸지 않는다. 이번 작업에서는 GitOps 저장소와 클러스터 리소스를 수정하지 않았다. Jenkins의 GitOps 직접 커밋 방식으로 전환하려면 기존 Image Updater와 버전 기록 주체를 하나로 정한 뒤 별도로 변경해야 한다.

1. 루트 구조를 개인 브랜치에서 검토하고 새 Jenkins Script Path로 CI를 검증한다.
2. 기존 Job의 Script Path를 바꾸거나 별도 새 Job을 최초 1회 실행해 SCM polling을 등록한다. 별도 Job 방식이면 이전 발행 Job을 중지한다.
3. `apps/ai-gateway/health.py` marker를 바꿔 커밋·Push한다.
4. 수동 Build/Sync 없이 Jenkins 실행과 이미지 Push, GitOps digest 커밋을 확인한다.
5. Argo CD GUI에서 새 GitOps 커밋의 `Synced/Healthy`, Pod 이미지 digest 및 `/cicd-healthz` marker를 확인한다.

복귀 시 새 발행 Job을 중지한 뒤 Script Path를 `infra/app/Jenkinsfile`로 되돌린다. 이미 GitOps 버전이 바뀌었다면 이전 이미지 digest도 함께 복원하고 Image Updater가 다시 덮어쓰지 않도록 조정한다.

## 로컬 검증

```bash
python -m venv .ci-venv
.ci-venv/bin/python -m pip install -r requirements-dev.txt
.ci-venv/bin/python -m pytest -q
python scripts/ci.py validate
git diff --check
```

Windows에서는 `.ci-venv/Scripts/python.exe`를 사용한다. 테스트는 CI의 선택 빌드·실패/비발행 조건, 공통 모듈의 빌드 포함 여부와 bot 권한 정책을 확인한다. Collector의 DB 통합 테스트는 DB 준비 후 별도로 실행한다. 실제 Docker 빌드·Registry Push 및 Argo CD rollout은 서버에서 검증해야 한다.

서비스 6개 requirements의 pip dry-run 해석과 Docker 빌드 컨텍스트·COPY 입력, 앱 Python 문법을 로컬에서 확인했다. Docker Desktop Linux 엔진이 실행 중이지 않아 실제 Docker 빌드는 수행하지 못했고, Jenkins의 Python 3.12 실행·클러스터 배포도 로컬 검증에 포함하지 않는다.
