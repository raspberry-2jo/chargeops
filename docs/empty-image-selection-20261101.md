# 빌드 이미지 0개 처리

## 문제와 수정

Jenkins에서 빈 문자열 환경변수가 Agent 셸에 없을 수 있다. `set -u` 상태에서
`$BUILD_IMAGES`를 직접 읽으면 `unbound variable`로 중단된다.
현재 chargeops-ci가 사용하는 루트 Jenkinsfile의 모든 셸 참조를 `${BUILD_IMAGES:-}`로 변경하고, Groovy 단계 조건에는
`HAS_SELECTED_IMAGES` 문자열 플래그(`true`/`false`)를 사용한다.

## 수정한 셸 참조 전체 목록

총 7개 sh 블록, 11개 참조다.

| Stage / sh 블록 | 변경한 참조 | 개수 |
|---|---|---:|
| Test (Python 3.12) | reset의 빈 이미지 검사 | 1 |
| Docker Build | 이미지 검증 루프, 선택 이미지 case | 2 |
| Trivy Quality Gate | run --images | 1 |
| Registry Push | verify --images, 고유 태그 Push 루프, dev 태그 루프 | 3 |
| ECR mirror push / 사전 검사 | verify --images | 1 |
| ECR mirror push / 복사 | 이미지 tag/push 루프 | 1 |
| Development delivery and integration test | 빈 이미지 검사, prepare --images | 2 |

## 실행 범위

아래는 게시 브랜치에서 Environment CD와 Canary, ECR 옵션을 켠 경우다.

| 단계 | 이미지 0개 | CSMS 1개 | notifier 1개 |
|---|---|---|---|
| 전체 Python 테스트 | 실행 | 실행 | 실행 |
| Docker Build·Trivy·Registry Push·ECR | 생략 | 실행 | 실행 |
| Dev 검증·통합 검사 | 현재 Digest로 실행 | 새 CSMS 검증 | 기존 CSMS와 선택 앱 조합 검증 |
| Source PR 생성·승인 대기 | 생략 | 실행 | 실행 |
| Ops promote | 생략 | 실행 | 실행 |
| Ops Canary | 생략 | 실행 | 생략 |
| Ops 일반 배포 검증 | 생략 | 기존 Canary 흐름 | 실행 |

선택 결과가 0개면 생략 이유를 Detect 단계에서 출력한다. 이전의 open-ci-pr 경로는 제거했다.
Dev 실패 시에도 0개 빌드는 이미지를 변경하지 않았으므로 이미지 복구 경로를 실행하지 않는다.

## Dev 검증 전용 모드

`env_cd.py prepare --verify-current`는 내부 선택을 빈 목록으로 고정한다.
선택 이미지를 함께 넘기거나 Canary를 켜면 거부한다. 기존 Dev의 고정 Digest와
GitOps fingerprint를 candidate에 기록하고, 기존 verify가 정확한 이미지 준비 상태와
통합 검사 Job을 검증한다. 성공 판정 항목을 줄이거나 생략하지 않는다.
GitOps pin·commit과 새 태그 Digest 조회를 실행하지 않는다. candidate/verification
산출물은 이 빌드용으로 만들되 이전 production-delivery.json 기록은 보존한다.

## 검증 범위

- 실제 Jenkins 셸을 BUILD_IMAGES 환경변수 없이 실행하는 회귀 테스트.
- 모든 sh 블록의 안전하지 않은 BUILD_IMAGES 참조 정적 검사.
- 0개·CSMS·notifier 선택별 실제 when 표현식 평가 및 Canary 조건 검사.
- 현재 Digest 검증 전용 모드에서 pin·commit·새 Digest 조회 호출 금지 및 통합 검증 실행.
- Jenkins 컨트롤러에서의 실제 stage 생략/실행은 다음 새 빌드에서 확인해야 한다.

최종 전체 pytest 결과: **437 passed**. Docker 입력과 앱 Python 문법 검증도 통과했다.
