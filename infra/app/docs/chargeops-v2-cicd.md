# ChargeOps v2 CI/CD 적용 기록과 실행 절차

> 원본 저장소의 적용 기록입니다. 현재 앱 경로, Jenkins SCM/Script Path와 배포 절차는
> [infra/app 이전 및 CI/CD 연결 문서](../../../docs/infra/app-cicd-migration.md)를 따릅니다.
> 현재 로컬 작업 브랜치는 `kim_chanju`이며, 원격 Push와 서버 설정 변경은 수행하지 않았습니다.

적용일: 2026-10-14. 로컬 `main`의 기준 커밋은 `0007900`입니다.
작업 경로: `C:\Users\SBA_USER\codex-test\git-test\chargeops`.

## 적용 내용

- `Downloads/chargeops-lab-v2.zip`의 `chargeops-lab/apps/`만 가져왔습니다. ZIP의 Jenkinsfile, Vagrant, Ansible, DB, Helm 파일은 옮기지 않았습니다.
- ZIP SHA256: `bde088e518723aec04b218c01ebc96e2364e21ba2f009dd093c05c6149c9bdf9`.
- 여섯 서비스에 전용 Dockerfile과 requirements.txt를 추가했습니다. 빌드 컨텍스트는 `apps/`입니다. 서비스별 빌드로 대체된 공용 apps/Dockerfile, 공용 apps/requirements.txt, Collector Containerfile은 삭제했습니다.
- `/cicd-healthz`는 `ai_gateway.health:app`에서 제공합니다. 최초 marker는 `ci-cd-probe-v1`입니다.
- Jenkins는 기존 세 이미지와 새 여섯 이미지를 지원합니다. 첫 실행, Jenkinsfile 변경, `BUILD_ALL_IMAGES=true`는 전체 빌드입니다.
- 그 외 변경은 이전 성공 커밋부터 비교합니다. `apps/common/`은 AI gateway, v2 bot, notifier를 선택합니다. 앱별 폴더는 해당 이미지 하나를 선택합니다. 미등록 `apps/` 경로는 여섯 앱, 나머지 미등록 경로는 아홉 이미지를 선택합니다.
- 기존 테스트 단계는 루트 requirements 및 `tests/`를 실행합니다. ZIP 여섯 앱의 업무 기능 테스트까지 포함한 것은 아닙니다.
- `.ci-venv`와 로컬 Secret 파일을 Git/Docker 컨텍스트에서 제외했습니다.

| 폴더 | Docker Registry 이미지 | 실행 형태 |
|---|---|---|
| ai_gateway | registry.chargeops.lab:5000/chargeops/chargeops-ai-gateway | HTTP 8000 |
| chatops_bot | registry.chargeops.lab:5000/chargeops/chargeops-v2-chatops-bot | Slack Socket Mode |
| csms | registry.chargeops.lab:5000/chargeops/chargeops-v2-csms | HTTP 8000 / OCPP 9000 / metrics 9100 |
| notifier | registry.chargeops.lab:5000/chargeops/chargeops-v2-notifier | HTTP 8000 / 알림 폴링 |
| vcharger | registry.chargeops.lab:5000/chargeops/chargeops-v2-vcharger | OCPP 클라이언트 / 제어 8081 |
| collector | registry.chargeops.lab:5000/chargeops/chargeops-v2-collector | 배치 |

## 첫 빌드

Linux `mgmt-agent`에 Python 3.12, venv, Git, Docker CLI/Engine을 준비합니다.
앱 빌드 Jenkins는 인증 없는 Registry를 사용하며 Docker 로그인·로그아웃 및 Registry credential 설정을 사용하지 않습니다.
이미지는 `registry.chargeops.lab:5000/chargeops/` 아래로 Push합니다. 먼저 Python 베이스 이미지 두 태그를 복제합니다.
이미지 발행 브랜치는 `dev`입니다. Jenkins Job의 SCM 브랜치를 `*/dev`로 설정하며, PR 빌드와 다른 브랜치 빌드에서는 Push하지 않습니다.
Jenkins 에이전트와 클러스터에서 Registry DNS·접속 및 TLS/HTTP 설정을 확인하고, 별도 GitOps 저장소의 이미지 주소와 Image Updater 설정도 함께 변경합니다.

Job `chargeops-ci`: Pipeline script from SCM / Git / `https://github.com/chanjook1m/chargeops.git` / `*/main` / `Jenkinsfile`.

아래 커밋과 Push는 최초 이미지를 만들 준비가 되었을 때 PowerShell에서 실행합니다.

```powershell
Set-Location 'C:\Users\SBA_USER\codex-test\git-test\chargeops'
git diff --check
git status --short
git add Jenkinsfile apps .dockerignore .gitignore docs/chargeops-v2-cicd.md
git commit -m "feat: add ChargeOps v2 independent images and selective CI"
git push origin main
```

첫 실행은 Build Now로 시작합니다. 성공 후 아홉 이미지의 `dev` 및 `build-*` 태그를 확인합니다.
그 다음 별도 infra-test 저장소의 `docs/chargeops-v2-gitops.md` 순서로 GitOps 변경을 올립니다.
Jenkins는 GitOps 커밋 또는 kubectl/Helm 배포를 수행하지 않습니다.

## 자동 배포 확인: 변경은 한 번만

최초 배포 후 `/cicd-healthz`에서 v1을 확인하고 다음 변경을 **한 번만** 실행합니다.
원문 Step 5-1과 5-3의 동일한 v1 → v2 Push는 중복이므로 이 절차로 합쳤습니다.

```powershell
Set-Location 'C:\Users\SBA_USER\codex-test\git-test\chargeops'
$file = Join-Path (Get-Location) 'apps\ai_gateway\health.py'
$text = [IO.File]::ReadAllText($file)
if (-not $text.Contains('ci-cd-probe-v1')) { throw 'marker가 v1인지 확인하세요. 이미 v2이면 변경을 반복하지 않습니다.' }
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($file, $text.Replace('ci-cd-probe-v1', 'ci-cd-probe-v2'), $utf8)
git diff --check
git diff -- apps/ai_gateway/health.py
git add apps/ai_gateway/health.py
git commit -m "chore: verify ChargeOps GitOps rollout"
git push origin main
```

Build Now 없이 polling을 기다립니다. Jenkins는 AI gateway 이미지 하나만 선택해야 합니다.
Docker Registry digest 변경 → infra-test digest 커밋 → Argo CD Sync → 새 Pod의 marker v2 응답을 확인합니다.
Pod가 교체되면 기존 port-forward가 끊길 수 있으므로 새로 실행합니다.

## 로컬 검토의 범위

패키지 고정 버전은 PyPI에서 존재 여부를 확인했습니다. Docker 빌드, Jenkins 실행,
Docker Registry Push, 클러스터 배포, DB/Ollama/Slack 기능은 로컬 적용 단계에서 실행하지 않았습니다.
실제 Secret은 저장소에 추가하지 않았습니다. 초기 적용 커밋 `fc93416`은 main에 Push했습니다.
로컬 Windows Python 3.13.15에서 기존 테스트 2개가 통과했고, ai-gateway의 `/healthz`와
`/cicd-healthz`는 HTTP 200 및 marker v1을 반환했습니다. 실제 Jenkins·클러스터 검증은 연결 후 수행해야 합니다.

## 이전 수동 배포 파일 정리

현재 Jenkins는 모든 이미지에 서비스별 Dockerfile을 명시합니다. 중복된 루트 Dockerfile과
이전 수동 배포용 `k8s/` 디렉터리, 해당 디렉터리에만 의존하는 `scripts/render_manifest.py`를 제거했습니다.
배포 매니페스트는 별도 infra-test 저장소의 Kustomize 경로에서 관리합니다.
기존 서비스의 Secret/ConfigMap 준비용 `scripts/setup_k8s.py`는 유지합니다.
