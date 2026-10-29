# Debian 13 미수정 취약점 검토 및 기한부 위험 수용

검토일: 2026-10-29. 만료: **2026-11-05 00:00 UTC**, 자동 연장 없음.
담당: ChargeOps maintainers. 승인 근거: 이 작업에서 사용자가 요청한 검토 후 조건부 예외 허용.
최초 대상: AI Gateway. 2026-10-29 추가 요청에 따라 아래 6개 서비스로 검토 범위를 확장했다.
근거 입력은 Jenkins #71 AI Gateway 44건/8 CVE와 #73 ChatOps Bot 44건/8 CVE+Python5건 목록이다.
원본 전체 JSON 파일을 직접 입수한 것은 아니므로 실제 JSON의 OS/Class/Type 및 패키지 일치는 다음 Jenkins 실행에서 검증한다.

## 1. 공급자 판정

Debian tracker의 trixie(Debian 13)에서 아래 8개는 vulnerable로 기록되어 있다.
util-linux/ncurses/systemd는 no-dsa(Minor issue), acl은 point release 반영 예정의 no-dsa,
Perl은 upstream 회귀 문제 정리를 기다리는 postponed이다. **no-dsa는 안전/패치 완료라는 의미가 아니다.**
sid/forky에 수정 버전이 존재하더라도 Debian 13에 그 버전이 제공된다는 뜻은 아니다.
운영 이미지에 sid 저장소를 섞거나 검증 없이 Alpine으로 전환하지 않는다.

| CVE | 공격 조건 / 공급자 근거 | 이 이미지에서 적용하는 완화 |
|---|---|---|
| [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642) | 로컬 mount helper 실패 후 privileged post-mount hook | mount/umount 제거, SUID/SGID 제거 |
| [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408) | 권한 있는 운영자의 nsenter --join-cgroup 실행 | nsenter 제거 |
| [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409) | fstab 허용 X-mount.subdir 및 특정 커널 경로 | mount/umount 제거 |
| [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) | fstab bind mount 경로를 교체하는 로컬 공격 | mount/umount 및 SUID/SGID 제거 |
| [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369) | privileged caller가 공격자 제어 경로에 ACL 작업 | 코드에서 ACL/외부 프로세스 호출 미발견, UID 10001, SUID/SGID 제거. libacl 라이브러리 자체는 남으며 잔여 위험 기한부 수용 |
| [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) | infocmp CLI의 문자열 분석 | infocmp 제거 |
| [CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742) | systemd-homed 관리 계정의 로컬 로그인 | homed/homectl 제거, Python 서비스 UID 10001 |
| [CVE-2026-9538](https://security-tracker.debian.org/tracker/CVE-2026-9538) | Perl Archive::Tar가 악성 tar header 처리 | Perl 실행 파일 제거; 앱은 Perl/tar 처리 호출 없음 |

## 2. 악용·노출 범위

CISA 공식 [kev-data](https://github.com/cisagov/kev-data)의 develop JSON을 직접 내려받아
8개 ID를 대조했다. catalogVersion=2026.10.28, dateReleased=2026-10-28T19:00:04.8077Z,
SHA256=c72e8b7751209e7575ed777439e3f4e9295ba86b4edf5d52730959d8bf9bfe64. 일치 ID=0.
KEV 미등재는 미악용/무위험 증명이 아니며 신규 악용 정보가 나오면 예외를 재검토한다.

검토한 앱: apps/ai-gateway/app.py, health.py, apps/common/db.py.
HTTP API는 summarize/embed/ask 및 health이며 Python requests/FastAPI/psycopg를 사용한다.
mount/nsenter/infocmp/Perl/ACL 호출 및 subprocess/os.system 경로를 찾지 못했다.
앱은 HTTP로 접근되므로 인터넷 노출이 없다고 가정하지 않는다. 현재 ingress/방화벽/노드 권한은
이 PC에서 실클러스터 접근이 없어 확인하지 못했다. 기존 GitOps base는 runAsNonRoot와 UID/GID10001을
선언하지만 allowPrivilegeEscalation:false/drop ALL까지 적용됐다고 주장하지 않는다.
이 판정은 **제한된 위험 수용**이며 전체 시스템의 비영향(VEX not_affected) 선언이 아니다.

## 3. 이미지 자체 완화와 실행 증거

Dockerfile은 useradd 이후 root 단계에서 runtime_security --harden을 실행한다.
불필요한 도구 실행 파일을 제거하고 /usr의 일반 파일에서 SUID/SGID 비트를 제거한다.
패키지 DB/SBOM을 삭제하지 않는다. libacl 등 런타임 라이브러리는 의존성 파손을 피하려고 강제 삭제하지 않는다.
이 변경으로 컨테이너 내 mount/Perl/터미널 진단 도구는 사용할 수 없다. 관리 진단은 별도 도구 컨테이너를 사용한다.

USER10001 이후 도구 부재·SUID/SGID 부재·실제 앱 import를 확인한다.
예외를 적용할 때 Jenkins는 **스캔한 immutable image ID**를 network none/read-only/drop ALL/
no-new-privileges로 실행해 같은 증거를 다시 얻는다. 실행 실패/누락 증거는 차단한다.
이 검증의 Docker 실행 옵션이 실제 Kubernetes에 자동 적용되는 것은 아니다.
실제 클러스터 권한은 배포 운영 점검 대상으로 남는다.

## Jenkins 판정

- security/trivy-exceptions.json의 이미지+Debian13+os-pkgs/debian+정확한 CVE/패키지/설치 버전+HIGH만 일치.
- FixedVersion이 비어 있어야 하며, 만료일 전이어야 한다. CRITICAL 승격/새 CVE/다른 버전/다른 OS/다른 이미지/Python/config는 허용하지 않음.
- 원본 HIGH/CRITICAL JSON은 그대로 보존한다. --ignore-unfixed 및 CVE ignore 파일로 숨기지 않음.
- Trivy exit42는 findings 전용이며 정책 평가로 이어진다. exit1 등 스캔 오류는 보고서가 있어도 차단.
- gate.json의 reviews에 각 예외 사유/담당/만료와 실제 runtime_evidence 기록.
- Jenkins 콘솔에 ACCEPTED CVE/package/until 표시, build description과 summary.txt에 예외 건수 표시.
- Push 전 policy hash, image ID, 원본 JSON, 만료 여부 및 이미지 완화를 재검증.
- 예외 만료 시 해당 취약점이 남아 있으면 실패. 수정 버전이 제공되면 업데이트 전까지 실패.
- 전체 6개 이미지와 config가 모두 정책을 통과해야 Push/Dev 검증/승인/Canary로 진행한다.
  아래 명시된 6개 이미지 외에는 예외를 허용하지 않는다. 새로운 CVE/패키지/버전의 예외는 자동 추가하지 않는다.

## 2026-10-29: 나머지 5개 이미지까지 확대 검토

사용자 요청: 나머지 이미지에도 전부 동일하게 확인.
모두 동일한 사설 Python3.13/Debian 베이스, USER10001을 사용한다.
다음 소스의 import/프로세스 실행 경로를 확인했으며 mount/nsenter/infocmp/Perl/ACL/외부 프로세스 실행 호출을 찾지 못했다.
Collector의 별도 테스트 코드 subprocess는 실제 Dockerfile이 복사하는 collector_v1.py에 포함되지 않는다.

| 이미지 | 검토한 런타임 소스와 사용 경로 | 외부 연결 검증의 한계 |
|---|---|---|
| chargeops-ai-gateway | ai-gateway/app.py, health.py, common: HTTP/DB/Ollama | 기존 health import 검사 유지; DB/LLM 업무 호출 별도 |
| chargeops-chatops-bot | chatops-bot/app.py, common: Slack SocketMode/HTTP/DB | app import가 실제 Slack token/auth를 요구하므로 빌드에서 앱을 시작하지 않음 |
| chargeops-csms | csms/*.py: FastAPI/OCPP/WebSocket/DB/Prometheus | DB_DSN과 lifespan 네트워크를 빌드에서 시작하지 않음 |
| chargeops-notifier | notifier/app.py, common: HTTP/Slack/SMTP/DB | 알림 발송/DB 기록은 별도 통합 검증 |
| chargeops-vcharger | virtual-charger/vcharger.py: OCPP 클라이언트/제어 HTTP | 실제 충전기 접속은 Dev 시험 |
| chargeops-collector | collector/collector_v1.py: HTTP/psycopg/S3 boto3 | 실제 수집·S3·DB 적재는 별도 시험 |

각 서비스 Dockerfile은 의존성 설치 → pip check → pip/setuptools/wheel 제거 → 소스 복사 →
useradd → 위험 실행 도구/SUID/SGID 제거 → USER10001 → runtime_security <image-name> 순서이다.
공통 검사 파일은 apps/common/runtime_security.py이며 CSMS/VCharger/Collector에도 파일 하나만 직접 복사한다.
common DB 코드를 강제로 포함시키지 않는다. 기존 CMD/ENTRYPOINT/앱 동작은 유지한다.

5개 requirements에도 setuptools84.2.1(설치 단계), msgpack1.2.3, urllib32.8.0을 고정한다.
Python 취약점은 예외 대상이 아니다. pip SBOM/내부 라이브러리만 숨기지 않고 빌드 도구 자체를 제거한다.
런타임에서 pip install/pip check를 실행할 수 없으며 필요한 의존성은 이미지 빌드 때 설치해야 한다.

공통 검사는 모든 직접 의존성 및 수정한 패키지의 import, msgpack/urllib3 버전,
빌드 도구 모듈/metadata 부재, 앱 소스 compile, UID/도구/SUID 상태를 확인한다.
네트워크 연결을 시작하거나 실제 Slack token을 요구하지 않는다. 실제 Bot 인증·메시지 발송·DB/OCPP
기능을 통과했다고 보고하지 않는다. Jenkins의 Dev/승인/Canary 검증은 유지한다.

정책은 기존 8개 CVE/정확한 패키지·버전을 6개 이미지에 명시적으로 매핑한다.
알려지지 않은 이미지·다른 버전·새 CVE·수정 가능한 항목·Python/config·만료는 계속 차단한다.
나머지 4개의 실제 Trivy 결과는 아직 제공되지 않았다. 동일 항목이 실제로 발견되고 각 이미지의
완화 실행 증거까지 통과할 때만 예외가 활성화되며 전체 통과를 미리 보장하지 않는다.

## 검증과 남은 작업

로컬 단위 테스트는 실제 policy evaluator와 gate의 Docker adapter를 통해 44건 허용 및
새 CVE/수정 가능/다른 이미지/버전/만료/CRITICAL/Python/config 차단을 검사한다.
스캔 오류·완화 검증 실패·정책 변경 시 Push 차단도 검사한다.
Docker daemon/원격 Agent 접근이 없어 실제 새 이미지/Trivy/HTTP 검증은 **아직 미실행**이다.
기존 #71의 runtime import 성공은 새 완화 이미지의 실행 성공을 보장하지 않는다.

다음 Jenkins 실행에서 Docker Build의 exception_check와 Trivy gate의 runtime_evidence,
원본 JSON/summary.txt를 확인한다. 성공 이후에도 Dev 통합 검사와 승인/Canary는 유지된다.
패치 제공·앱 실행 경로 변경·KEV 등 신규 정보가 생기면 기한 전에도 검토하고, 예외 연장은 별도 근거로 수행한다.
