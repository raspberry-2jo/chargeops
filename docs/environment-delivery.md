# Dev 검증과 승인 후 Ops 이미지 승격

루트 Jenkinsfile의 기존 6개 이미지 Build/Push는 유지한다. 환경 CD는 CSMS부터 적용하며 초기 설정 전에는 `ENABLE_ENVIRONMENT_CD=false`가 기본이다.

관리 서버 초기 설정과 롤백 절차는 GitOps 저장소의 [환경 배포 문서](https://github.com/raspberry-2jo/chargeops-gitops/blob/main/docs/environment-delivery.md)를 따른다.

## Jenkins 실행

- 앱 저장소 branch: `kim_chanju`, Script Path: `Jenkinsfile`, agent: `mgmt-agent`.
- `ENABLE_ENVIRONMENT_CD=true`: CSMS를 빌드한 경우 Dev digest 갱신 → Dev 동기화·읽기 통합 테스트 → admin 승인 → Prod digest 변경.
- `RUN_OCPP_FIXTURE_TEST=false`: 기존 DB 설정을 그대로 사용하므로 API/DB 읽기만 기본 검증한다. OCPP 쓰기 테스트는 별도 승인한 개발 충전기 fixture가 있을 때만 켠다.
- `PUBLISH_DEV_TAG=false`가 기본이다. 고유 build 태그는 발행하되 기존 `:dev` 태그를 움직이지 않는다. 운영 digest 고정과 Dev-only ImageUpdater 적용을 끝낸 뒤 필요한 경우 true로 설정한다. 환경 CD는 false 상태에서도 고유 build 태그를 사용하므로 동작한다.
- Credentials: `chargeops-gitops-write`(Username/Password), `chargeops-dev-kubeconfig`(Secret file). 기본 ID는 Job 파라미터에서 변경할 수 있다.
- Python 3.12, venv, Docker, kubectl이 agent에서 필요하다. PyYAML/pytest는 requirements-dev.txt로 설치한다.
- `artifacts/candidate.json`, `artifacts/verification.json`을 Jenkins에 보관한다. 실제 토큰·DB DSN은 포함하지 않는다.
- Prod는 승인된 동일 digest로 Git 설정만 변경된다. 실제 Ops 배포는 운영 Argo CD Application에서 수동 Sync한다.
- Dev 이미지·설정 또는 운영 기준 설정이 승인 대기 중 바뀌면 중단한다. 이미지 재빌드·가변 태그 재조회로 승격하지 않는다.
- Git Push 충돌은 강제로 덮어쓰지 않고 실패 처리한다. `.gitops-delivery`에 충돌/미커밋 변경이 남았다면 원인을 검토하고 깨끗한 전용 작업공간으로 재실행한다.

환경 CD가 꺼져 있거나 CSMS 이미지가 빌드되지 않으면 기존 CI만 실행한다. 신규 Dev Namespace와 DB 설정을 담은 Secret은 관리 서버에서 먼저 준비해야 한다. 로컬에서 실제 Jenkins·클러스터 실행을 확인한 것은 아니다.
