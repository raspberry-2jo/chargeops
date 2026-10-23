# Dev 검증과 승인 후 Ops 이미지 승격

루트 Jenkinsfile의 기존 6개 이미지 Build/Push는 유지한다. 환경 CD는 CSMS부터 적용하며 초기 설정 전에는 `ENABLE_ENVIRONMENT_CD=false`가 기본이다.

관리 서버 초기 설정과 롤백 절차는 GitOps 저장소의 [환경 배포 문서](https://github.com/raspberry-2jo/chargeops-gitops/blob/main/docs/environment-delivery.md)를 따른다.

## Jenkins 실행

- 앱 저장소 branch: `kim_chanju`, Script Path: `Jenkinsfile`, agent: `mgmt-agent`.
- `ENABLE_ENVIRONMENT_CD=true`: CSMS를 빌드한 경우 Dev digest 갱신 → Dev 동기화·읽기 통합 테스트 → admin 승인 → Prod digest 변경.
- `ENABLE_CANARY=false`가 기본이다. GitOps의 초기 전환 후 true로 설정하면 승인된 CSMS를 25% → 50% → 100% Pod 비율로 배포하고 가용성 검사 실패 시 중단·Git 복구·재동기화한다. [Canary 초기 전환·테스트 안내](https://github.com/raspberry-2jo/chargeops-gitops/blob/main/docs/canary-delivery.md)를 따른다.
- `RUN_OCPP_FIXTURE_TEST=false`: 기존 DB 설정을 그대로 사용하므로 API/DB 읽기만 기본 검증한다. OCPP 쓰기 테스트는 별도 승인한 개발 충전기 fixture가 있을 때만 켠다.
- `PUBLISH_DEV_TAG=false`가 기본이다. 고유 build 태그는 발행하되 기존 `:dev` 태그를 움직이지 않는다. 운영 digest 고정과 Dev-only ImageUpdater 적용을 끝낸 뒤 필요한 경우 true로 설정한다. 환경 CD는 false 상태에서도 고유 build 태그를 사용하므로 동작한다.
- Credentials: `chargeops-gitops-write`(Username/Password), `chargeops-dev-kubeconfig`(Secret file). 기본 ID는 Job 파라미터에서 변경할 수 있다.
- Canary에는 `bootstrap-canary.py`가 생성하는 `chargeops-canary-kubeconfig`(Secret file)가 추가로 필요하다. 기존 Dev 계정에 운영 쓰기 권한을 추가하지 않는다.
- Python 3.12, venv, Docker, kubectl이 agent에서 필요하다. PyYAML/pytest는 requirements-dev.txt로 설치한다.
- Registry digest 조회는 HTTPS를 사용하고 환경변수 프록시를 경유하지 않는다. 시스템 CA 또는 `/etc/docker/certs.d/registry.chargeops.lab:5000/ca.crt`를 사용하며, CA가 다른 위치에 있으면 agent에 `CHARGEOPS_REGISTRY_CA`로 경로를 지정한다.
- `artifacts/candidate.json`, `artifacts/verification.json`, `artifacts/production-delivery.json`을 Jenkins에 보관한다. 실제 토큰·DB DSN은 포함하지 않는다. 미완료 Canary 복구 기록은 다음 빌드가 삭제하지 않는다.
- `ENABLE_CANARY=false`이면 Prod는 승인된 동일 digest로 Git 설정만 변경되고 실제 Ops 배포는 수동 Sync한다. true이면 승인 후 Jenkins가 기존 운영 Application에 CSMS만 선택적으로 Sync하며 Rollouts가 지표를 검사한다.
- Dev 이미지·설정 또는 운영 기준 설정이 승인 대기 중 바뀌면 중단한다. 이미지 재빌드·가변 태그 재조회로 승격하지 않는다.
- Git Push 충돌은 강제로 덮어쓰지 않고 실패 처리한다. `.gitops-delivery`에 충돌/미커밋 변경이 남았다면 원인을 검토하고 깨끗한 전용 작업공간으로 재실행한다.

환경 CD가 꺼져 있거나 CSMS 이미지가 빌드되지 않으면 기존 CI만 실행한다. 신규 Dev Namespace와 DB 설정을 담은 Secret은 관리 서버에서 먼저 준비해야 한다. 로컬에서 실제 Jenkins·클러스터 실행을 확인한 것은 아니다.
# Canary 완료 판별과 대기 로그

`scripts/canary_cd.py`는 요청한 Git revision, 이번 Sync의 고유 식별자, Sync 성공, 대상 이미지의 안정 Rollout 준비 상태를 모두 확인한다. `operation.info`의 `chargeops-operation-id`로 요청과 결과를 연결하며, `initiatedBy.username`만으로 실행자를 추정하지 않는다. 이전 요청과의 호환을 위해 전용 식별자가 없는 경우에만 정확한 기존 username 표식을 확인한다. 임의의 `admin` 성공 기록은 완료로 인정하지 않는다.

대기 중에는 `own_sync`, `sync_succeeded`, `rollout_ready`, `deployment_image_matches`를 상태 변경 시 또는 약 30초마다 출력한다. 요청 식별자는 `artifacts/production-delivery.json`의 `operation_id` 또는 `recovery_operation_id`에 저장된다. 이 값은 비밀 토큰이 아닌 작업 상관 식별자다.

실행 중인 Jenkins에는 나중에 Push한 스크립트 수정이 반영되지 않는다. 현재 코드는 기본 20분 관찰 한도 초과나 Abort 시 Git 복구를 시도한다. 따라서 수정본을 실행하기 전에 현재 빌드의 post 복구까지 종료됐는지 확인하고, 복구 미완료 receipt를 임의로 삭제하지 않는다.

