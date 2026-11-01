# 선택된 이미지 병렬 빌드

여러 이미지가 선택되면 mgmt-agent의 동일 Docker 엔진에서 최대 2개씩 동시에 빌드한다.
Jenkins `IMAGE_BUILD_PARALLELISM` 파라미터로 1(순차), 2(기본), 3, 4, 6 중 선택한다.
Agent 추가 또는 다른 Agent로의 자동 분산은 포함하지 않는다.

## 처리 순서

1. 기존 변경 경로 판정으로 선택된 이미지만 대상으로 한다.
2. 이미지 이름과 동시 실행 수를 검증한 후 Docker 빌드를 시작한다.
3. 동시 실행 수만큼 한 묶음으로 실행하고, 해당 묶음의 모든 빌드가 종료된 후 다음 묶음을 실행한다.
4. 하나라도 실패하면 나머지 실행 중 빌드의 종료를 기다린 후 실패 코드를 반환한다. 이후 묶음, Trivy, Push, 배포는 진행하지 않는다.
5. 모든 빌드가 성공하면 기존 Trivy 이미지·설정 검사를 수행한다.
6. 전체 검사 통과 및 기존 영수증 검증 후에만 Registry Push와 ECR mirror를 수행한다.

선택 이미지가 하나면 하나만 빌드한다. 0개면 기존과 같이 빌드·검사·Push를 생략한다.
Job 자체의 `disableConcurrentBuilds()`는 유지하므로 서로 다른 빌드 번호의 배포가 겹치지 않는다.

## 검사와 배포

Trivy는 tar 파일과 캐시를 공유하므로 기존 순차 방식을 유지한다. 보안 정책, 예외 정책,
선택 경로, 검증할 Digest 조합, Dev·Ops 판정, Canary 적용 범위는 변경하지 않는다.
셸 종료 시 남은 백그라운드 Docker 클라이언트에 종료 신호를 보내고 회수한다.
Jenkins 강제 종료 시 원격 Docker 엔진의 작업 종료 여부까지 이 코드만으로 보장하지는 않는다.

## 첫 빌드 확인

- Jenkins Build with Parameters에서 기본값 `IMAGE_BUILD_PARALLELISM=2`로 실행한다.
- `Image builds: maximum concurrency=2 (same mgmt-agent)`와 이미지별 시작 로그를 확인한다.
- `BUILD_ALL_IMAGES=true`를 선택하면 이미지 6개로 전체 병렬 경로를 확인할 수 있다. 이 옵션은 이후에도 매번 전체 빌드하므로 필요할 때만 선택한다.
- Docker Build 단계 수행 시간을 이전의 동일 이미지·캐시 조건과 비교한다.
- CPU, 메모리, 디스크 I/O가 부족하거나 시간이 늘면 동시 실행 수를 1로 내려 비교한다.

빌드 출력은 동시에 기록되어 섞일 수 있다. Jenkins 화면의 이미지별 독립 parallel stage는 생성하지 않으며,
기존 Docker Build stage 내부에서 셸 프로세스를 병렬 실행한다.
성능 향상 폭은 실제 Agent에서 측정해야 한다. 로컬 검증은 Docker 대역으로 동시성 상한,
빌드 겹침, 실패 시 대기 및 이후 작업 차단을 재현한다.
