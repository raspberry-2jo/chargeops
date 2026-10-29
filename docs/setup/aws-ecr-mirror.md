# AWS ECR 이미지 미러 설치 기록 (Jenkins → ECR)

- 담당: 황채연 (AWS) · Jenkinsfile 적용은 김찬주 (CI/CD)
- 관련 WBS:  없음 (AWS 추가 작업 · ECR 이미지 미러)
- 버전: versions.md 참고 (mgmt-agent AWS CLI v2.38.x)
- 상태: **2026-10-27 빌드 #59에서 이미지 6개 ECR 업로드 확인** (Jenkinsfile `ECR mirror push` 단계, PR #62)
- 관련: [`aws-s3-backup.md`](aws-s3-backup.md) (DB 백업도 같은 방식: 사내 repo1 + AWS repo2)

## 1. 왜 / 무엇을

- 앱 이미지 원본이 사내 레지스트리(`registry.chargeops.lab:5000`) 하나뿐이다. 레지스트리나 mgmt 디스크가 고장 나면 이미지도 같이 사라진다.
- DB 백업처럼 **이미지도 AWS에 사본**을 둔다. Jenkins가 사내 레지스트리에 올린 직후 **ECR(서울)에도 같은 이미지를 한 번 더 올린다(미러)**.
- 배포는 지금처럼 사내 레지스트리에서 받는다. ECR은 사본 + 취약점 스캔 용도다.

```text
Jenkins (mgmt-agent)
  docker build ─┬─▶ registry.chargeops.lab:5000/chargeops   (기존 · 배포용)
                └─▶ ECR <계정>.dkr.ecr.ap-northeast-2.amazonaws.com/chargeops   (추가 · 원격 사본 · 스캔)
```

**자동 전환(레지스트리 고장 → ECR에서 배포)은 하지 않는다.** 쿠버네티스가 ECR에 로그인해야 하고(토큰이 12시간마다 만료), ArgoCD·GitOps 이미지 주소와 카나리 흐름까지 바꿔야 해서다. 레지스트리가 고장 나면 사람이 판단해서 ① ECR에서 받아 사내 레지스트리를 복구하거나 ② 배포 주소를 잠시 ECR로 바꾼다.

| 구성 | 이름 | 비고 |
|---|---|---|
| ECR 저장소 6개 | `chargeops/chargeops-{ai-gateway, chatops-bot, csms, notifier, vcharger, collector}` | 서울 · 태그 덮어쓰기 금지 · push 때 스캔 · 최근 10개 보관 |
| IAM 정책 | `chargeops-ecr-push` | ECR 로그인 + chargeops 저장소에 올리기만 |
| IAM 사용자 | `chargeops-jenkins-ecr` | 콘솔 로그인 없음 · 액세스 키 1개 |
| Jenkins 비밀값 | `chargeops-ecr-push` · `chargeops-ecr-registry` | 키 · ECR 주소 (계정 번호가 들어가서 Git에 안 씀) |
| Jenkinsfile | 파라미터 3개 + `ECR mirror push` 단계 | `Registry Build and Push` 바로 뒤 |

**최소 권한** — 키가 새도 피해가 "chargeops 저장소에 이미지 올리기"에서 끝나게:

| 권한 | 범위 | 왜 |
|---|---|---|
| `ecr:GetAuthorizationToken` | `*` (AWS 규칙상 범위 지정 불가) | `docker login` 용 토큰 받기 |
| `ecr:BatchCheckLayerAvailability` · `InitiateLayerUpload` · `UploadLayerPart` · `CompleteLayerUpload` · `PutImage` | `repository/chargeops/*` | 이미지 올리기 |
| `ecr:BatchGetImage` | `repository/chargeops/*` | 올린 이미지 확인 |

저장소 삭제 · 다른 서비스(S3 등)는 주지 않는다.

## 2. 설치 명령어

### 2-1. ECR 저장소 6개 (AWS 콘솔 CloudShell, 서울)

```bash
# ===== 실행할 명령어 =====
# 저장소 6개 만들기 (태그 덮어쓰기 금지 · push 때 취약점 스캔)
for N in ai-gateway chatops-bot csms notifier vcharger collector; do aws ecr create-repository --region ap-northeast-2 --repository-name chargeops/chargeops-$N --image-tag-mutability IMMUTABLE --image-scanning-configuration scanOnPush=true --query repository.repositoryUri --output text; done
# 저장소마다 최근 10개만 보관 (비용이 계속 쌓이지 않게)
for N in ai-gateway chatops-bot csms notifier vcharger collector; do aws ecr put-lifecycle-policy --region ap-northeast-2 --repository-name chargeops/chargeops-$N --lifecycle-policy-text '{"rules":[{"rulePriority":1,"description":"keep last 10","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":10},"action":{"type":"expire"}}]}' --query repositoryName --output text; done
```

- 첫 명령은 저장소 주소 6줄, 두 번째는 저장소 이름 6줄이 나오면 성공.
- 태그 덮어쓰기 금지(IMMUTABLE)라도 문제없다. Jenkins 태그 `build-<브랜치>-<번호>-<커밋>`은 매번 다르다.

### 2-2. IAM 정책 · 사용자 (CloudShell)

```bash
# ===== 실행할 명령어 =====
# 정책 파일 (계정 번호는 명령이 채움)
ACC=$(aws sts get-caller-identity --query Account --output text); printf '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},{"Effect":"Allow","Action":["ecr:BatchCheckLayerAvailability","ecr:InitiateLayerUpload","ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage","ecr:BatchGetImage"],"Resource":"arn:aws:ecr:ap-northeast-2:%s:repository/chargeops/*"}]}' "$ACC" > ecr-push-policy.json && python3 -m json.tool ecr-push-policy.json >/dev/null && echo POLICY_JSON_OK
aws iam create-policy --policy-name chargeops-ecr-push --policy-document file://ecr-push-policy.json --query Policy.Arn --output text
aws iam create-user --user-name chargeops-jenkins-ecr --query User.Arn --output text && aws iam attach-user-policy --user-name chargeops-jenkins-ecr --policy-arn "arn:aws:iam::$ACC:policy/chargeops-ecr-push" && echo ATTACHED
```

권한 시험 (읽기만) — 올리기는 allowed, 그 밖은 implicitDeny 여야 한다:

```bash
aws iam simulate-principal-policy --policy-source-arn "arn:aws:iam::$ACC:user/chargeops-jenkins-ecr" --action-names ecr:GetAuthorizationToken ecr:PutImage ecr:DeleteRepository s3:ListAllMyBuckets --resource-arns "arn:aws:ecr:ap-northeast-2:$ACC:repository/chargeops/chargeops-csms" --query "EvaluationResults[].[EvalActionName,EvalDecision]" --output table
```

### 2-3. 액세스 키 → Jenkins 비밀값 (AWS 콘솔 · Jenkins 화면)

1. AWS 콘솔 → IAM → 사용자 `chargeops-jenkins-ecr` → 보안 자격 증명 → **액세스 키 만들기** → 사용 사례 "AWS 외부에서 실행되는 애플리케이션" → **.csv 다운로드** (비밀 키는 이때 한 번만 보임 · 캡처 금지 · 터미널에서 만들지 않음)
2. Jenkins → Jenkins 관리 → Credentials → System → Global credentials → **자격 증명 추가** 2번:

| 종류 | ID | 값 |
|---|---|---|
| 사용자 이름과 비밀번호 | `chargeops-ecr-push` | 사용자 이름 = 액세스 키 ID, 비밀번호 = 비밀 키, **사용자 이름을 비밀로 처리** 체크 |
| 비밀 텍스트 | `chargeops-ecr-registry` | `<계정>.dkr.ecr.ap-northeast-2.amazonaws.com/chargeops` (끝에 `/` 없음) |

3. 등록이 끝나면 .csv는 지우거나 비밀번호 관리자로 옮긴다. 키는 DM · 채팅으로 넘기지 않는다 (등록할 사람이 직접 입력).

### 2-4. Jenkins 에이전트 확인 (mgmt, 읽기만)

```bash
# ===== 실행할 명령어 =====
# 빌드를 실제로 돌리는 계정 찾기 (SSH 에이전트는 agent.jar 가 아니라 remoting.jar 로 보임)
ps -eo user,cmd | grep -i -E "[r]emoting|[a]gent\.jar|[j]enkins" | cut -c1-120
# 그 계정으로 aws 실행 · PATH · 저장된 키 확인
sudo -u jenkins-agent /usr/local/bin/aws --version
sudo cat /proc/$(pgrep -u jenkins-agent -f remoting.jar | head -1)/environ | tr '\0' '\n' | grep '^PATH='
sudo ls -la /home/jenkins-agent/.aws 2>&1 | head -3
```

- 빌드 계정 `jenkins-agent`, aws CLI v2, PATH에 `/usr/local/bin`, `.aws` 없음(저장된 키 없음 → Jenkins가 넘기는 ECR 키만 씀) 이면 설치할 것 없음.
- 키를 `aws configure`로 서버에 저장하지 않는다.

### 2-5. Jenkinsfile (PR #62)

`parameters { }` 끝(`OPS_ARGO_APPLICATION` 다음)에 3개:

```groovy
booleanParam(name: 'ECR_MIRROR', defaultValue: true,
  description: 'Also push published images to AWS ECR (copy only; deployment still uses REGISTRY).')
string(name: 'ECR_CREDENTIAL_ID', defaultValue: 'chargeops-ecr-push',
  description: 'Username/password: AWS access key ID / secret access key (ECR push only).')
string(name: 'ECR_REGISTRY_CREDENTIAL_ID', defaultValue: 'chargeops-ecr-registry',
  description: 'Secret text: <account>.dkr.ecr.ap-northeast-2.amazonaws.com/chargeops')
```

`stage('Registry Build and Push: changed images only')` 바로 뒤에 단계 1개:

```groovy
stage('ECR mirror push') {
  when { expression { params.ECR_MIRROR && env.PUBLISH_IMAGES == 'true' && env.BUILD_IMAGES?.trim() } }
  steps {
    catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
      withCredentials([
        usernamePassword(credentialsId: params.ECR_CREDENTIAL_ID.trim(),
          usernameVariable: 'AWS_ACCESS_KEY_ID', passwordVariable: 'AWS_SECRET_ACCESS_KEY'),
        string(credentialsId: params.ECR_REGISTRY_CREDENTIAL_ID.trim(), variable: 'ECR_REGISTRY')
      ]) {
        sh '''
          set -eu
          export AWS_DEFAULT_REGION=ap-northeast-2
          ECR_HOST="${ECR_REGISTRY%%/*}"
          trap 'docker logout "$ECR_HOST" >/dev/null 2>&1 || true' EXIT
          aws ecr get-login-password | docker login --username AWS --password-stdin "$ECR_HOST"
          for NAME in $BUILD_IMAGES; do
            docker tag "$REGISTRY/$NAME:$IMAGE_TAG" "$ECR_REGISTRY/$NAME:$IMAGE_TAG"
            docker push "$ECR_REGISTRY/$NAME:$IMAGE_TAG"
          done
        '''
      }
    }
  }
}
```

| 동작 | 이유 |
|---|---|
| 이미지를 올리는 빌드(`PUBLISH_IMAGES`)에서, 바뀐 이미지만 | 사내 레지스트리에 올린 것과 같은 이미지 · 같은 태그 |
| `ECR_MIRROR` 끄면 건너뜀 | 켜고 끄기 스위치 |
| `catchError` → ECR 실패 시 빌드는 **UNSTABLE(노랑)** | 기존 배포 흐름을 멈추지 않음 |
| `trap … logout` | 중간에 실패해도 에이전트에 ECR 로그인 정보를 남기지 않음 |
| ECR 주소를 비밀 텍스트로 | 계정 번호를 Git에 안 올림 · 로그에서도 가려짐 |

**적용 시점** — Jenkins `chargeops-ci`는 `kim_chanju` 브랜치로 빌드한다(`*/kim_chanju`, 5분마다 확인). dev에 머지된 뒤 **kim_chanju에 dev를 합쳐야** 적용된다. Jenkinsfile이 바뀌면 다음 빌드는 이미지 6개를 전부 빌드한다.

## 3. 설정 파일

- `Jenkinsfile` — 파라미터 3개 + `ECR mirror push` 단계. 비밀값 없음.
- 키 · ECR 주소는 Jenkins Credentials(`chargeops-ecr-push`, `chargeops-ecr-registry`)에만 있다.
- AWS: ECR 저장소 6개, IAM 정책 `chargeops-ecr-push`, 사용자 `chargeops-jenkins-ecr`.

## 4. 확인 방법

**Jenkins** — 이미지가 바뀐 빌드 → Stages에서 `ECR mirror push`가 초록. Console Output에 `Login Succeeded`와 이미지마다 `digest: sha256:…`.

```bash
# ===== 실행할 명령어 ===== (CloudShell)
for N in ai-gateway chatops-bot csms notifier vcharger collector; do aws ecr describe-images --region ap-northeast-2 --repository-name chargeops/chargeops-$N --query "imageDetails[].[imageTags[0],imagePushedAt]" --output text; done
# 취약점 개수 (심각도별) · TAG = 확인할 태그 (예: build-kim_chanju-59-4991247c09c6)
TAG=build-kim_chanju-59-4991247c09c6
for N in ai-gateway chatops-bot csms notifier vcharger collector; do echo "== $N"; aws ecr describe-image-scan-findings --region ap-northeast-2 --repository-name chargeops/chargeops-$N --image-id imageTag=$TAG --query "imageScanFindings.findingSeverityCounts" --output text 2>&1 | tail -1; done
```

```
# ===== 실행 결과 (2026-10-27, 빌드 #59) =====
None    2026-10-27T05:35:17.882000+00:00
None    2026-10-27T05:35:17.887000+00:00
build-kim_chanju-59-4991247c09c6    2026-10-27T05:35:18.108000+00:00
... (6개 저장소 모두 같은 태그 · 14:35:17 ~ 14:35:51 KST, 6개 약 35초)
```

- 저장소마다 태그 1개 + `None` 2개가 정상이다. Docker(buildx)가 이미지 1개를 **목록표(태그) + 실제 이미지 + 빌드 출처 정보** 3개로 올린다.
- 취약점 스캔 결과는 아직 기록 전.

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| `sudo -u <계정> …`에서 `-bash: 계정: No such file` | 자리표시(`<…>`)를 그대로 붙여 넣음 → 실제 값(`jenkins-agent`)으로 바꿔서 실행 |
| `sudo tr … < /proc/PID/environ` → Permission denied | `<` 파일 읽기는 sudo보다 먼저 일반 사용자 권한으로 실행됨 → `sudo cat /proc/PID/environ \| tr …` |
| `agent.jar` 로 찾으면 Jenkins 에이전트가 안 보임 | SSH 방식 에이전트는 `remoting.jar` 로 돎 → 검색어를 넓힘 |
| 빌드 #58 빨강, ECR에 #58 이미지 없음 | ECR 앞 단계에서 실패(ECR 문제 아님). ECR 단계만 실패하면 노랑(UNSTABLE)이다 |
| ECR 결과에 태그 없는 `None` 줄 | buildx 출처 정보(attestation) 때문 · 정상. 그래서 "최근 10개" 보관 규칙은 **실제로는 빌드 3번분** 정도 → 더 남기려면 숫자를 30으로 |

**남은 결정** — 지금 순서는 "사내 레지스트리 push → ECR push"라서, **사내 레지스트리가 고장 나면 ECR에도 안 올라간다.** 고장 때도 ECR에 남기려면 ECR 단계를 앞에 두거나 사내 push 실패와 상관없이 ECR을 시도하게 바꿔야 한다 (CI/CD 담당과 결정).

## 6. 정리 (프로젝트 끝)

1. IAM → `chargeops-jenkins-ecr` → 액세스 키 **비활성화 → 삭제**
2. Jenkins Credentials `chargeops-ecr-push` · `chargeops-ecr-registry` 삭제, Jenkinsfile `ECR_MIRROR` 기본값 `false` (또는 단계 제거)
3. ECR 저장소 6개 삭제 (이미지 포함): `aws ecr delete-repository --region ap-northeast-2 --repository-name chargeops/chargeops-<이름> --force`
4. IAM 사용자 · 정책 `chargeops-ecr-push` 삭제

비용: 저장 GB당 월 약 0.10달러, 올리는 전송은 무료 (청구서로 확인).
