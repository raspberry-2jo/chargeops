# 이미지 비교 기준 커밋 복구

## 문제

기존 Jenkinsfile은 `checkout scm` 반환값의 이전 성공 SHA를 명시적으로 환경변수에
복사하지 않고, 셸의 `GIT_PREVIOUS_SUCCESSFUL_COMMIT` 자동 설정에 의존했다.
전달된 #88 사례에서는 마지막 성공 빌드가 있는데도 셸에서 값이 비어 전체 빌드로 처리됐다.
실제 #88 서버 재현은 이 PC에서 하지 않았다.

## 변경

1. 주 애플리케이션 checkout 직후 `checkoutVars.GIT_PREVIOUS_SUCCESSFUL_COMMIT`을
   `CHECKOUT_PREVIOUS_SUCCESSFUL_COMMIT`에 저장한다.
2. 없으면 `${JOB_URL}/lastSuccessfulBuild/api/json`에서 Git BuildData의
   `lastBuiltRevision.SHA1`을 조회한다. `remote.origin.url`과 `remoteUrls`가 일치하는
   항목만 사용해 GitOps SHA와 혼동하지 않는다. 여러 SHA가 나오면 기준을 포기한다.
3. 전체 40자리 SHA만 허용하고 `git cat-file -e <sha>^{commit}`으로 확인한다.
4. 로컬에 없으면 `git fetch --no-tags origin <sha>`를 한 번 시도하고 다시 확인한다.
   checkout·브랜치는 바꾸지 않는다. Git 명령은 각각 최대 45초, API는 15초다.
5. 검증된 SHA만 `GIT_PREVIOUS_SUCCESSFUL_COMMIT`으로 넘긴다. 없으면 빈 문자열을
   넘겨 기존 image_selection.py의 전체 빌드 규칙을 그대로 사용한다.

`scripts/image_selection.py`의 경로→이미지 판정 코드는 변경하지 않는다.
BUILD_ALL_IMAGES 강제 전체 빌드 동작도 유지한다.

## Jenkins API 읽기 권한

Checkout 값이 있으면 API 인증은 필요 없다. API fallback이 필요하고 익명 읽기가
차단돼 있으면 Jenkins Credentials에 Job Read 권한 계정의 사용자명/API Token을
Username with password 형식으로 등록하고 `JENKINS_READ_CREDENTIAL_ID`에 ID를 지정한다.
기본값은 빈 문자열이며 익명 조회한다. GitHub 토큰을 Jenkins API 인증에 재사용하지 않는다.
스크립트는 토큰·API 본문·원격 URL을 로그나 산출물에 기록하지 않고 리다이렉트도 따라가지 않는다.
API 403/404, 잘못된 응답, 저장소 불일치, 조회 실패는 전체 빌드로 처리한다.
Private Git fetch는 Agent에서 사용 가능한 Git 인증에 의존한다. 인증이 없어 fetch가 실패해도
전체 빌드로 처리하며, checkout 이후 Git 인증이 자동으로 유지된다고 가정하지 않는다.

## 다음 빌드 확인

정상 로그 예시:

```text
Image comparison base=<40자리 SHA> source=checkout_vars fetch=not_needed reason=verified
Image comparison base=<40자리 SHA> source=jenkins_api fetch=fetched reason=verified
Image comparison base=(none) source=none fetch=not_needed reason=api_http_403
Image comparison base unavailable; building all images
```

`artifacts/comparison-base.json`을 Jenkins 산출물에 보관한다. 기준 SHA, 출처, fetch
시도와 fallback 이유를 확인하고 `artifacts/image-selection.json`의 base와 비교한다.
기준을 얻었다는 이유만으로 특정 이미지가 생략된다고 보장하지 않는다. 그 이후 실제 변경
경로와 기존 Dockerfile 의존성 판정에 따라 선택된다.

## 로컬 검증 결과

- 전체 pytest: 431 passed.
- 기준 커밋 전용 테스트 10개: checkout 우선, API fallback, 403/404, 입력 부재,
  로컬 커밋 부재와 fetch 성공·실패, 저장소 구분, Jenkins 연결, 실제 shallow clone 복구.
- `scripts/ci.py validate` 통과. `scripts/image_selection.py` 변경 없음 확인.
- 실제 Jenkins #88 재실행과 서버 API 인증 검증은 다음 빌드에서 확인해야 한다.
