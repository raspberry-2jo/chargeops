# Dev namespace CSMS Canary

앱 저장소 dev 브랜치 승격이나 Ops 배포와는 별개의, Kubernetes dev namespace 실습이다.
기존 Jenkins 입력 ENABLE_CANARY는 Ops 정책이다. 새 DEV_DELIVERY_MODE는 Dev 배포 방식을 뜻한다.
기본값 auto는 chargeops-dev Application의 namespace/path/repository를 검증한 후 기존 Deployment 또는 새 Canary overlay를 선택한다.
기존 Dev가 전환되기 전에는 기존 동작을 유지한다. 전환 후 SCM 빌드도 자동으로 Canary 경로를 사용한다.

## Jenkins 동작

새 Dev Canary는 Deployment/csms replicas=0을 템플릿 원본으로 두고 Rollout/csms가 정상 Pod 2개를 관리한다.
Jenkins는 현재 Rollout의 generation, workload generation, stable/current hash, 정확한 이미지,
Rollout 소유 ReplicaSet과 그 소유 Ready Pod 2개를 검사한다. 0/0 Deployment 자체는 실패가 아니다.
Rollout abort/Degraded가 현재 candidate에 속한 경우 검증을 조기에 중단한다.

Dev Canary 검증 실패 시 post에서 기존 GitOps 쓰기 자격 증명과 dev 전용 kubeconfig로 recover-dev를 실행한다.
현재 Git 이미지/설정이 실패 candidate와 여전히 일치할 때만 development-canary/kustomization.yaml의 image를 이전 digest로 되돌린다.
다른 사람이 새 Dev 이미지나 설정을 넣었으면 덮어쓰지 않는다. Ops image/file/credential은 사용하지 않는다.
Argo 자동 동기화 후 이전 이미지의 Rollout Healthy와 정확한 Pod 2개를 확인해야 recovered 영수증을 쓴다.
artifacts/dev-recovery.json은 Jenkins에 보관한다. 미완료 복구 영수증은 다음 빌드에서 삭제하지 않는다.
복구가 끝나도 실패한 배포의 Pipeline 결과는 실패로 남는다. PR 생성/운영 승격 단계는 실행되지 않는다.

## 수동 Dev 복구 재개

미완료 복구가 있으면 같은 Jenkins workspace/후보 영수증과 GitOps checkout에서 실행한다.
KUBECONFIG는 dev 전용 파일, Git 환경 자격 증명은 기존 Jenkins withCredentials를 사용한다.
명령줄에 토큰이나 비밀번호를 넣지 않는다.

```bash
export DEV_GITOPS_OVERLAY=chargeops-chatops/overlays/development-canary
.ci-venv/bin/python scripts/env_cd.py recover-dev
```

## 별도 실패 시연과의 구분

GitOps scripts/dev-canary.py rehearse는 같은 CSMS 이미지의 템플릿 annotation만 바꾸어 새 RS를 만든다.
검사 Job이 canary 대상에서 20회마다 한 번 잘못된 읽기 전용 URL을 호출해 실제 CSMS 404를 관측한다.
이 시연은 Job 오류율 → Analysis 실패 → Rollout abort → 원래 템플릿 복귀를 확인한다.
새 애플리케이션 버그나 readiness 장애를 주입하는 기능은 아니다. DB/OCPP 쓰기나 Ops 호출은 없다.
시연의 원래 템플릿 복귀는 전용 스크립트가 담당한다. Jenkins의 Git digest 복구는 실제 CI 후보 배포 실패 경로이며
로컬 테스트는 실제 임시 Git 저장소에서 image-only 복원과 더 최신 이미지 보호를 검증한다.
서버에서 Jenkins 복구까지 검증했다고 주장하려면 해당 빌드의 실패/복구 로그와 recovered 영수증이 추가로 필요하다.


## Dev verification timing

Dev Canary convergence defaults to 900 seconds (15 minutes); legacy Deployment verification and Dev recovery retain 600 seconds. An explicit --timeout overrides this. The Jenkins development stage allows 25 minutes, including checkout, image preparation and the separate integration Job wait (210 seconds).

While waiting, verification prints Argo sync/health and Canary phase, step, ready replicas and current analysis state approximately every 30 seconds. An aborted candidate still fails immediately and enters the existing Dev recovery path.
