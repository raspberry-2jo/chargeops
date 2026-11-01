# Docker COPY 기반 이미지 선택과 검증 전용 Dev 실행

## 원인

기존 Jenkinsfile은 일부 CI 파일 변경에 전체 이미지를 선택했고, CD가 활성화되면
선택 결과에 CSMS를 무조건 추가했다. `prepare --images ...`도 CSMS를 무조건
새 build 태그로 조회·고정했다. 이 때문에 scripts-only 빌드가 이미지 작업을
완전히 건너뛸 수 없었다.

## 새 선택 규칙

`scripts/image_selection.py`가 Jenkinsfile의 실제 `build_if_selected` 선언에서
이미지 이름·Dockerfile·context를 읽고 각 Dockerfile의 로컬 COPY/ADD 입력을
추출한다. 별도 앱 폴더→이미지 하드코딩 맵을 제거했다.

- 비교: `GIT_PREVIOUS_SUCCESSFUL_COMMIT..HEAD`, 삭제·rename 양쪽 경로 포함.
- 이전 성공 커밋 없음·조회 실패·파싱 불확실성: 모든 이미지 선택.
- 수동 `BUILD_ALL_IMAGES=true`: 기존 파라미터 그대로 전체 선택.
- Dockerfile 자체 변경(FROM 변경 포함): 해당 이미지 선택.
- `COPY common/ ...`: 폴더 아래 전체 파일에 의존.
- `COPY common/runtime_security.py ...`: 그 파일만 의존.
- context `.dockerignore` 변경: 그 context를 쓰는 이미지 선택.
- Dockerfile별 `.dockerignore` 변경: 해당 이미지 선택.
- Git diff에서 CLI의 경로·확장자 필터로 파일을 버리지 않는다. 문서도 실제 COPY
  안에 들어 있으면 이미지 입력이므로 선택한다. root docs/scripts/tests/Jenkinsfile
  변경만으로는 선택하지 않되, Jenkinsfile의 이미지 빌드 선언 변경은 전체 선택한다.
- JSON COPY, 연속행, 일반 COPY 옵션을 지원한다. 동적 변수·glob·symlink·RUN mount·ONBUILD·
  해석 불가능한 입력은 조용히 줄이지 않고 전체 빌드한다.

| 변경 경로 | 현재 Dockerfile 기준 선택 |
|---|---|
| apps/csms/app.py | CSMS만 |
| apps/common/runtime_security.py | 6개 모두 |
| apps/common/policy.py 등 나머지 common 파일 | AI Gateway·ChatOps Bot·Notifier |
| apps/collector/unused.py | 없음 — COPY되지 않는 파일 |
| scripts/env_cd.py, Jenkinsfile의 CI 로직, root tests/docs | 없음 |
| 앱 Dockerfile / FROM 줄 | 해당 앱 |
| 이전 성공 커밋 없음·찾지 못함 | 6개 모두 |

원격 레지스트리에서 같은 FROM 태그를 덮어쓴 사실은 Git diff만으로 알 수 없다.
FROM 문자열/digest를 Git에 갱신하거나 베이스 이미지 갱신 시 BUILD_ALL_IMAGES로
재빌드한다. 원격 태그 변화까지 자동 검출했다고 주장하지 않는다.

이미지별 선택 원인 파일 또는 전체 선택 사유를 콘솔에 출력하고
`artifacts/image-selection.json`을 항상 아카이브한다.

## 빌드 대상 0개

Docker Build·Trivy·Registry Push·ECR mirror의 기존 비어 있지 않은 BUILD_IMAGES
조건을 유지한다. Dev handover 확인과 Development delivery/integration stage는
CSMS 빌드 여부에 종속시키지 않는다. 기존 전체 단위 테스트와 CI 구성 검증을
계속 실행한다.

`prepare --images ""`는 현재 Dev의 pinned CSMS Digest를 사용한다. 새 태그를
Registry에서 조회하지 않고, Dev image 파일·baseline 시각도 수정하거나 commit/push
하지 않는다. 기존 `verify`와 **동일한 integration Job 정의**로 검증한다.
반복 Rebuild도 새 Job을 만들 수 있도록 build_tag를 후보 fingerprint에 포함한다.
검증 전용 후보의 실패로 기존 Dev 이미지를 rollback하지 않는다.

대상 0개는 기존 CI-only source PR 경로를 사용하고 Ops 승인 대기·승격·Canary
실행은 건너뛴다. 직접 promote를 호출해도 `built_images=[]` 후보는 승격을 거부한다.

## prepare / promote Digest 감사 결과

1. 이전 prepare는 --images에 CSMS가 없어도 새 태그 Digest를 조회·고정했다.
   이제 **CSMS가 실제 선택된 경우에만** 그 새 태그를 조회·Dev에 고정한다.
2. 다른 선택 앱은 새 태그를 resolve하되, 기존처럼 CSMS-only Dev에 그 앱을 새로
   설치하지 않는다. 미선택 앱은 새 태그를 resolve하거나 Digest를 변경하지 않는다.
3. `images`: 새로 선택된 앱과 Dev에서 검증할 CSMS anchor의 pinned Digest.
4. `development_images`: 실제 Dev에 존재하는 앱 Digest 조합. CSMS 외 이미지를
   새로 설치·교체하거나 검사 완료라고 위장하지 않는다.
5. `release_images`: 현재 Ops의 기존 앱 Digest 조합 + 선택 앱의 새 Digest +
   Dev CSMS anchor. Collector의 구/신 repository alias는 같은 앱 identity로
   비교하고, 같은 앱의 CronJob들이 서로 다른 Digest면 중단한다.
6. 검증 receipt는 후보 전체 fingerprint에 바인딩한다. 유지할 기존 Digest를
   포함해 어떤 조합이든 바꾸면 기존 receipt로 승인·승격할 수 없다.
7. promote는 Dev fingerprint/실제 CSMS 준비 상태/검사 Job UID·성공/승인 조건을
   그대로 재확인한다. 선택된 앱을 고정한 뒤 **전체 Ops Digest 조합**을 후보의
   release_images와 비교하고 일치할 때만 GitOps commit/push한다.
8. 다른 앱의 Ops baseline이 승인 대기 중 바뀌면 기존 production fingerprint
   검사로 중단한다. 다른 빌드의 변경을 섞어서 새 조합으로 승격하지 않는다.

**검증 범위:** 현재 기존 Dev는 CSMS 전용이다. 다른 앱은 빌드·Trivy 검증 및
Digest 조합 고정 대상이며 Dev 런타임 통합 검사를 통과한 앱이라고 말하지 않는다.
기존 GitHub PR 설명도 이 제한을 명시하고 있으며 유지한다. 다른 5개 앱까지 Dev에
배포해 런타임 검사하는 구성은 이번 범위에 포함하지 않는다.

추가 Canary 범위 규칙에 따라 non-CSMS-only 릴리스는 Ops CSMS를 현재 Digest로
유지한다. Dev CSMS anchor는 통합 검사에만 사용하고 Ops 승격 이미지에서 제외한다.
CSMS가 선택된 경우에만 CSMS Digest 승격과 Canary를 수행한다.
자세한 현재 규칙은 [Canary 적용 범위](canary-scope-20261101.md)를 따른다.

## 유지한 정책과 검증

Trivy 기준·예외 정책·스캔 옵션, pytest/통합 검사 항목, Canary 성공·실패 조건,
Stage 순서·병렬 구조·timeout은 바꾸지 않았다. 변경한 Stage 조건은 선택 결과
0개일 때 검증만 하고 비어 있지 않을 때 승인·Ops 승격하는 데 필요한 부분이다.

단위 테스트: 요청한 6가지 경로/기준 케이스, Dockerfile FROM 변경·삭제·강제 전체·
불확실 입력·JSON COPY, 실제 Jenkins shell 구문/선택기 실행, no-build Job 유지,
CSMS 미선택 시 Registry 조회/Dev 파일/커밋 유지, non-CSMS-only Ops 조합 승격,
조합 변조 receipt 거부, 반복 검증 Job의 고유 이름을 검증한다.

실제 Jenkins 또는 Kubernetes 배포는 이 PC에서 실행하지 않았다. 실제 다음 빌드에서
선택 로그, skipped stages, Dev integration PASS와 pinned Digest를 확인한다.

검증 결과: 기존 372개를 포함해 전체 **392개 통과**. Docker 입력·COPY 파일·
앱 Python 문법 검증도 통과했다. 이전 `318b34f..a557113` CI 폴링·로그 변경
범위를 새 선택기로 조회해 대상 0개인 것을 확인했다. Trivy 정책 파일,
canary_cd.py, 실제 CSMS 통합 검사 release_probe.py에는 변경이 없다.
