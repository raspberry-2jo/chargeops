# AI Gateway 빌드 도구 제거 실험

원본 테스트 브랜치: `codex/ai-gateway-runtime-cleanup`. 사용자 요청으로 `kim_chanju`에도 반영했습니다.
실제 이미지 실행·Trivy 재검사는 아직 완료되지 않았으며, 아래 Agent 검증과 Jenkins 품질 게이트 결과를 확인해야 합니다.

## 변경

- requirements의 setuptools를 78.1.1 → 84.2.1로 갱신합니다.
- Dockerfile 한 RUN에서 설치 → pip check → pip/setuptools/wheel 제거를 수행합니다.
- USER 10001:10001 전환 후 실제 최종 런타임에서 도구 모듈·distribution metadata 부재,
  직접 의존성 import, 실제 ai_gateway.health import와 경로 등록을 확인합니다.
- msgpack 1.2.3, urllib3 2.8.0 및 나머지 앱 버전은 유지합니다.
- 독립 wheel 버전을 새로 추가하지 않습니다. 제거 실험이므로 top-level wheel을
  추가 설치해 setuptools의 vendored wheel을 덮어썼다고 가정하지 않습니다.

requirements의 setuptools pin은 이 실험에서는 설치·빌드 단계에만 적용됩니다.
최종 런타임에서 해당 requirements 파일을 다시 pip로 적용하는 구성은 아닙니다.
도구 제거 후 `python -m pip check`는 실행할 수 없으며, pip check 통과만으로
제거 후 런타임의 정상 동작까지 보장하지 않습니다.

## Agent에서 실행

현재 실행 중인 Jenkins workspace를 바꾸지 말고 별도 테스트 checkout에서 실행합니다.
git status가 깨끗한 것을 확인하고 다음 브랜치를 checkout합니다.

```bash
git fetch origin codex/ai-gateway-runtime-cleanup
git switch --track origin/codex/ai-gateway-runtime-cleanup
bash scripts/test_ai_gateway_cleanup.sh
```

이미 로컬 테스트 브랜치가 있다면 `git switch codex/ai-gateway-runtime-cleanup`을
사용합니다. 스크립트는 Python3.12 테스트·CI 정적 검사·AI Gateway만 no-cache 빌드·
도구 제거 확인·실제 Uvicorn CMD·HTTP 응답을 검증합니다. 이미지 Push와 배포는 하지 않습니다.
이름이 고정된 테스트 이미지 태그를 덮어쓰므로 같은 Docker daemon에서 중복 실행하지 않습니다.

애플리케이션의 `/healthz`와 `/cicd-healthz`는 DB/LLM 연결을 확인하지 않습니다.
테스트 컨테이너는 network none이며 운영 Secret을 사용하지 않습니다. 실제 요약·검색·
임베딩·DB 기록 기능은 승인된 Dev 환경·시험 데이터로 추가 검증해야 합니다.

## 같은 조건의 Trivy 재검사

Agent에서 이미지 빌드가 성공한 뒤 실행합니다. 기존 before.json은 보존합니다.
보안 정책을 끄지 않도록 실제 Pipeline의 Trivy gate와 별도로 결과를 해석하세요.
아래 `--exit-code 0`은 전체 취약점 목록 비교를 위한 수집 모드이며 품질 게이트 통과가 아닙니다.

```bash
(
  set -Eeuo pipefail
  REPORT_DIR="$HOME/trivy-retest"
  mkdir -p "$REPORT_DIR" "$REPORT_DIR/cache"
  test ! -e "$REPORT_DIR/after-cleanup.json"
  IMAGE_TAR="$(mktemp "$REPORT_DIR/image.XXXXXX.tar")"
  trap 'rm -f -- "$IMAGE_TAR"' EXIT
  docker save --output "$IMAGE_TAR" chargeops-ai-gateway:trivy-cleanup-test
  docker run --rm --pull=never \
    -v "$REPORT_DIR:/scan:Z" \
    -v "$REPORT_DIR/cache:/root/.cache:Z" \
    aquasec/trivy:0.76.1 image --input "/scan/$(basename "$IMAGE_TAR")" \
      --scanners vuln --list-all-pkgs --format json --no-progress \
      --exit-code 0 --output /scan/after-cleanup.json
)
```




결과 비교 시 동일 Trivy 버전과 가능한 한 동일한 DB 조건을 사용합니다.
inventory를 원하면 `--list-all-pkgs`를 명시합니다. 일반 보고서는 Packages가 없을 수
있으므로 빈 jq 출력만으로 취약 패키지가 제거됐다고 판단하지 않습니다.

```bash
jq -r '
  .Results[]? as $result
  | $result.Packages[]?
  | select(.Name == "pip" or .Name == "setuptools" or .Name == "jaraco.context" or .Name == "wheel")
  | [.Name, .Version, ($result.Target // "-"), (.FilePath // "-")]
  | @tsv
' "$HOME/trivy-retest/after-cleanup.json"

jq -r '
  .Results[]? as $result
  | $result.Vulnerabilities[]?
  | [.VulnerabilityID, .PkgName, .InstalledVersion, .Severity,
     (.PkgPath // $result.Target // "-")]
  | @tsv
' "$HOME/trivy-retest/after-cleanup.json"
```

`pip: None`은 실행 환경에서 모듈을 찾지 못한다는 뜻입니다. 베이스 이미지의
기존 layer, ensurepip의 bundled wheel, 다른 위치에 복사된 vendored 코드까지
이미지 전체에서 모두 사라졌다는 뜻은 아닙니다. 최종 재검사에서 경로를 확인합니다.
SBOM 파일만 삭제하거나 CVE를 ignore하지 않습니다.

## 검증·병합 조건

로컬 Docker 엔진과 Agent 연결이 없어 실제 이미지·HTTP·Trivy 검증은 로컬에서
완료하지 못했습니다. 단위 테스트는 런타임 검증기의 성공·도구 잔존·의존성 누락 실패와
Dockerfile 실행 순서를 확인하는 모의 검증입니다. 모의 검증을 실제 패키지 호환성으로
보고하지 않습니다.

1. Agent에서 빌드 중 pip check 통과 및 최종 runtime_check 통과.
2. 실제 컨테이너 HTTP 헬스체크 통과.
3. 승인된 Dev 환경에서 사용하는 업무 API·DB·LLM 기능 검증.
4. before/after Trivy에서 해당 CVE·패키지 경로 비교. 다른 HIGH/CRITICAL도 함께 검토.
5. kim_chanju의 기존 CI·Trivy gate 결과를 확인하고, 실제 이미지·업무 기능 검증 전에는 운영 적용 완료로 판단하지 않음.

근거:
- https://pypi.org/project/setuptools/84.2.1/
- https://github.com/advisories/GHSA-58pv-8j8x-9vj2
- https://github.com/advisories/GHSA-8rrh-rw8j-w5fx
