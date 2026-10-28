# GitHub PR 생성 오류와 머지 후 Ops 재시도

## HTTP 오류 진단

`scripts/github_cd.py`는 GitHub 오류 JSON을 최대 16KiB까지 읽고 다음 고정 사유로 분류한다.

- 422: 변경 없음 / 기존 PR 존재 / 잘못된 head·base / 기타 요청 유효성 오류
- 401: 자격 증명 거부
- 403: 권한 거부 또는 rate limit
- 404: 리소스 또는 접근 권한 확인 필요
- 429: rate limit
- 5xx: GitHub 서비스 오류

GitHub의 원본 `message`, `errors`, `value`, 응답 본문·토큰은 로그에 출력하지 않는다.
알려진 resource·field·code 조합만 진단에 포함한다.

PR 목록 조회와 생성 사이에 다른 주체가 같은 PR을 만들면 POST가 422를 반환할 수 있다.
이때 열린 PR을 한 번 다시 조회하고 검증한 head가 일치할 때만 재사용한다.

## PR은 머지됐지만 Ops 카나리만 실패한 경우

같은 소스를 재실행하면 dev와 새 PR 차이가 없을 수 있다. 빈 커밋으로 새 PR을 강제로 만들 필요는 없다.
새 실행은 기존 흐름대로 Dev 검증을 수행한 후, PR 생성이 변경 없음으로 거부될 때 아래 조건을 확인한다.

1. 기존 PR이 같은 저장소의 kim_chanju → dev이며 정확한 source SHA로 머지됨.
2. 다른 사람의 최신 head 승인과 write/maintain/admin 권한이 현재도 확인됨.
3. CHANGES_REQUESTED 등 거부되는 리뷰 상태가 없음.
4. 기존 merge commit이 현재 dev에 포함되고 merge 후 apps 빌드 내용이 바뀌지 않음.
5. 검증한 source, 기존 PR merge, 현재 dev의 apps tree가 모두 같음.

모든 조건이 맞을 때 현재 실행의 PR 영수증에 `reused_merged_pr: true`를 기록한다.
`wait-merge`와 승격 직전에도 기존 승인과 apps 일치를 다시 검증한다. 머지된 PR을 수정·재오픈하지 않는다.

현재 구조는 전체 Jenkins 재실행을 지원하는 수정이다. 이미지 빌드를 생략하는 CD 전용 Job을 새로 만든 것은 아니다.
이번 실행의 Dev 검증 digest를 승격하며, 과거 검증 영수증만 가져와 바로 배포하지 않는다.

## 실행 전 확인

- 이전 Ops 배포가 정상 baseline으로 복구됐는지 먼저 확인한다.
- 진행 중인 다른 Argo Sync가 있으면 완료를 기다린다.
- `production-delivery.json` 삭제만으로 실제 클러스터 복구가 완료되지는 않는다.
- 수정된 kim_chanju 소스를 체크아웃하는 새 Jenkins 실행을 사용한다.
- dev에 다른 apps 변경이 들어왔다면 현재 dev를 작업 브랜치에 반영하고 새 소스로 검증한다.

소스가 바뀌지 않은 경우 로그에 `Reviewed merged PR reused for retry`가 표시된다.
dev에 해당 소스가 있어도 조건을 충족하는 승인 PR이 없으면 Ops 승격을 차단한다.
