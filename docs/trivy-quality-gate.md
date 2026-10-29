# Trivy Quality Gate · Jenkins 적용

Jenkins 감시 브랜치: `kim_chanju`. 이 변경은 기존 배포 승인과 Canary 정책을 바꾸지 않습니다.

실행 순서:

```text
Checkout → Test / Dev 준비 상태 검사 → Docker Build(선택 이미지 전부)
→ Trivy Quality Gate → Registry Push → ECR mirror push
→ Dev 검증 → 소스 PR 승인·merge → Ops GitOps → Canary
```

## 검사와 중단

- 대상: BUILD_IMAGES에 선택된 6개 서비스 중 모든 빌드 이미지. IMAGE_REF 하나만 검사하지 않습니다.
- 이미지: 로컬 이미지를 하나씩 `docker save` → Trivy `image --input`으로 검사합니다.
- 설정: 현재 checkout HEAD의 Git 추적 파일을 `git archive`로 준비해 Trivy config를 실행합니다.
- 승인된 기한부 예외 이외의 HIGH/CRITICAL, 실행·DB 다운로드·TLS·이미지 export·JSON 생성/검증 실패는 Stage를 실패시킵니다.
- 예외는 security/trivy-exceptions.json의 정확한 이미지·OS·CVE·패키지·버전과 만료일에 한정합니다. 자세한 근거와 완화 검증은 [Debian 검토 기록](security/trivy-debian-review-2026-10-29.md)에 있습니다.
- ignore-unfixed·실패 무시·스캔 비활성화는 사용하지 않습니다. 기본 trivy.yaml 로딩은 끄고 빈 ignore 파일을 명시해 원본 JSON을 보존합니다. Trivy findings 전용 exit42를 정책 평가하며, 스캐너 오류 exit1 등은 항상 차단합니다.
- 첫 실패에서 멈춥니다. 이미지 검사에서 실패하면 아직 실행하지 않은 이미지·config 보고서가 없을 수 있습니다.
- Registry Push와 ECR Push 앞에서 성공 receipt, source SHA, 선택 이미지 목록, tag, 로컬 image ID 및 JSON을 다시 확인합니다. Restart from Stage로 검사를 건너뛸 수 없습니다.
- ECR mirror 자체의 기존 UNSTABLE 정책은 유지하지만 Trivy 검증은 catchError 밖에서 실행하여 반드시 FAILURE로 차단합니다.

## 보고서

Jenkins build artifacts:

```text
trivy-reports/<BUILD_NUMBER>/chargeops-<서비스>.json
trivy-reports/<BUILD_NUMBER>/config.json
trivy-reports/<BUILD_NUMBER>/gate.json
trivy-reports/<BUILD_NUMBER>/summary.txt
```

Stage의 post/always가 JSON을 archive합니다. allowEmptyArchive=true는 실행 오류 때 보고서가 없을 가능성을 처리할 뿐 성공을 의미하지 않습니다. gate.json에 성공/실패와 전체 시간·이미지별 docker save 시간·tar 크기·scan 시간을 기록합니다. 같은 build를 재시작해도 이전 성공 receipt를 먼저 무효화합니다.

보통 예외·중단 시 tar와 임시 source는 finally/context manager로 정리합니다. SIGTERM은 실패로 처리하고 정리를 시도합니다. SIGKILL·에이전트 소실은 정리 코드 실행 자체를 보장하지 못하므로 남은 `.trivy-scan-*`는 실행 종료를 확인한 뒤 별도 점검합니다. 지속 cache `.trivy-cache`는 유지합니다.

## Agent 준비

실행 위치: 실제 Jenkins mgmt-agent 서버. Jenkins agent와 동일한 Docker daemon에서 확인합니다.

```bash
sudo -u jenkins-agent -H docker image inspect aquasec/trivy:0.76.1
```

이미지가 없으면 관리자가 정상 네트워크에서 먼저 준비합니다.

```bash
sudo -u jenkins-agent -H docker pull aquasec/trivy:0.76.1
```

Pipeline은 `--pull=never`를 사용하며 이미지가 없으면 실패합니다. Trivy DB·정책 다운로드용 외부 접근도 필요합니다. 기존 수동 검사의 cache 경로와 Jenkins의 WORKSPACE/.trivy-cache는 다를 수 있어 첫 스캔은 다운로드 시간이 추가됩니다.

Trivy 컨테이너에는 Docker socket·Registry 인증·전체 workspace를 넘기지 않습니다. scratch, cache, 현재 build의 보고서 디렉터리만 마운트합니다. 검사 이미지는 이미 로컬에 있으므로 private Registry CA를 Trivy에 마운트할 필요가 없습니다. Docker build/push 및 public DB 다운로드의 TLS·인증 문제는 각 경로에서 별도로 해결해야 합니다.

config 검사는 **앱 저장소의 Git 추적 Dockerfile/IaC** 범위입니다. 이후 Stage에서 checkout하는 별도 chargeops-gitops 저장소나 현재 클러스터 리소스를 검사한 것으로 해석하지 않습니다. GitOps 저장소 검사를 추가하려면 별도 범위·revision·반영 순서를 검토해야 합니다.

## 시간·용량

이미지 tar는 한 번에 하나만 유지합니다. 디스크에는 가장 큰 이미지 tar와 source archive를 저장할 여유가 필요합니다. Docker image inspect의 Size는 참고값이며 실제 tar 크기와 같다는 보장은 없습니다. cache와 JSON 보고서는 별도 용량을 사용합니다.

Trivy Stage의 제한은 30분이며 기존 Pipeline 전체 제한 120분은 유지합니다. cold/warm 실행 시간을 gate.json과 Jenkins Stage 시간으로 비교합니다. Agent가 두 대라면 cache도 각각 준비됩니다.

## 검증 범위

로컬 Docker 엔진이 실행되지 않아 실제 Trivy 컨테이너와 CVE DB를 이용한 스캔·Jenkins 서버 Declarative linter 실행은 이 작업에서 확인하지 못했습니다. 실제 Jenkins shell 블록의 Bash 문법 및 실행, gate의 성공·CVE/설정 위반·다운로드 오류·누락/손상 JSON·export 오류·누락 tool·중단·stale receipt·이미지 변경 차단을 자동 테스트합니다. 원격 Jenkins에서 첫 실행의 Trivy 단계와 artifacts를 확인해야 합니다.
