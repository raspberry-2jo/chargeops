# PR 승인 기반 Dev / Ops 배포

루트 Jenkinsfile을 사용한다. Dev는 namespace `dev`, 운영은 기존 `ops`를 유지한다. CI는 여섯 앱을 빌드하지만, 자동 환경 배포와 Canary는 **CSMS만** 대상으로 한다.

```text
kim_chanju Push → CI / 이미지 Push → CSMS 변경이면 Dev 배포·검증
                                      ↓
                         kim_chanju → dev PR 자동 생성
                                      ↓
                      다른 운영자의 최신 변경 승인 + Merge
                                      ↓
dev Jenkins Job → Merge 커밋 승인 확인 → CI / 이미지 Push
                                      ↓
                       CSMS Dev 재배포·통합 검증
                                      ↓
               검증한 동일 Digest로 GitOps main의 Ops 경로 변경
                                      ↓
                  Argo CD 선택 Sync → Ops Canary → 완료
                                      ↓ 실패
                  Abort → 이전 Git Digest 복원 → Sync·정상 검증
```

승인 PR은 **앱 저장소 chargeops의 kim_chanju → dev** 이다. GitOps main은 기존처럼 Jenkins가 직접 갱신하므로 자동 복구를 위한 쓰기 권한을 유지한다. 별도 Jenkins input 승인은 사용하지 않는다.

dev Job은 현재 dev HEAD가 PR Merge 커밋인지, PR 최신 HEAD를 작성자 외 사람이 승인했는지, 승인자가 저장소 쓰기 권한을 갖는지 확인한다. 직접 Push, 오래된 빌드, 자기 승인, 봇 승인, 이전 변경 승인은 Ops 승격을 허용하지 않는다. 승격 직전에도 승인과 Dev 검증 결과를 다시 확인한다.

**Merge된 최종 코드를 다시 빌드하고 Dev에서 검증한 동일 Digest를 Ops에 승격**한다. kim_chanju 검증 이후 다른 dev 변경과 합쳐진 결과를 검증하기 위한 절차다.

## 핵심 파일

| 파일 / 경로 | 역할 |
|---|---|
| Jenkinsfile | 브랜치별 CI, Dev 검증, PR 생성, Ops 승격, Slack 호출 |
| scripts/github_cd.py | PR 생성·재사용, 승인 검증, 커밋 상태 등록 |
| scripts/env_cd.py | Dev Digest 갱신, 통합 검증, 검증 결과에 묶인 Ops 승격 |
| scripts/canary_cd.py | Ops 선택 Sync, Canary 상태 확인, Abort·GitOps 자동 복구 |
| scripts/notify_ci.py | 허용된 배포 정보만 Slack으로 전송 |
| GitOps chargeops-chatops/overlays/development | namespace dev, CSMS 검증용 |
| GitOps chargeops-chatops/overlays/prod | namespace ops, 운영 Deployment 템플릿 |
| GitOps chargeops-chatops/overlays/canary | namespace ops, Rollout·Analysis·운영 템플릿 조합 |

예전 overlays/dev는 이름과 달리 Ops 경로다. namespace dev에는 overlays/development를 사용한다. Application 이름만으로 환경을 판단하지 말고 destination.namespace와 source.path를 확인한다.

## 기본 동작

- ENABLE_ENVIRONMENT_CD=true, ENABLE_CANARY=true. 운영 승격은 dev 브랜치에서만 실행한다. kim_chanju Job은 Ops를 변경하지 않는다.
- 이미지 태그는 build-브랜치-빌드번호-커밋12자리다. Jenkins Job 간 번호 충돌을 방지한다. 배포에는 Digest를 사용한다.
- PUBLISH_DEV_TAG=false. Jenkins가 GitOps Digest를 갱신하므로 Image Updater를 다시 켜지 않는다.
- RUN_OCPP_FIXTURE_TEST=false. 기존 DB 연결을 유지하며 기본 검증은 읽기만 수행한다. OCPP 쓰기 검증은 별도 승인된 테스트 충전기가 있을 때만 실행한다.
- Ops Application은 Manual Sync를 유지한다. 승인된 dev 빌드가 CSMS만 선택 Sync를 요청한다.
- 다른 앱은 이미지 빌드·Push와 소스 PR까지 자동화하며 운영 배포는 별도 작업이다.
- Registry 조회는 HTTPS와 신뢰된 CA를 사용한다. 별도 CA 경로는 agent의 CHARGEOPS_REGISTRY_CA로 지정한다.
- Git Push 충돌을 강제로 덮어쓰지 않는다. 미완료 복구 receipt를 임의로 삭제하지 않는다.
- candidate.json, verification.json, production-delivery.json, source-approval.json, pull-request.json을 Jenkins artifacts에 보관한다. Secret 값은 저장하지 않는다.
- Canary 완료는 자신의 Sync 식별자, Sync 성공, 목표 이미지, 안정된 Rollout을 함께 확인한다. Deployment csms의 replicas 0은 workloadRef 구조에서 정상이며 실제 네 개 Pod는 Rollout이 관리한다.

Slack은 CI 시작·최종 결과, Dev 검증 완료, PR 준비, Ops Canary 시작, 복구 완료·실패를 알린다. Slack 전송 실패는 운영 롤백의 원인으로 취급하지 않는다.

PR 생성 이후의 리뷰 승인·수정 요청·Merge 없는 종료·Merge 완료는 별도 `Jenkinsfile.pr-notifications` Job이 기존 GitHub/Slack Credential로 1분마다 확인한다. 이 관찰 Job은 배포하지 않는다. 최초 Job 생성 및 테스트는 아래 연결 설정 문서 6절을 따른다.

실제 적용 순서는 [PR 승인·Slack 연결 설정](pr-approval-slack.md)을 따른다. 로컬 검증이 서버 배포 성공을 의미하지는 않는다.

코드 변경 없이 CI가 반복되면 [SCM 반복 실행 점검](scm-polling.md)을 따른다. GitOps checkout은 poll=false로 제외하고 앱 저장소의 SCM 감시만 CI 트리거로 유지한다.
