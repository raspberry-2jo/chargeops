# GitHub Actions PR Slack 알림

대상: 같은 chargeops 저장소의 브랜치에서 **dev로 향하는 모든 PR**. kim_chanju에 한정하지 않는다. 외부 fork PR은 현재 대상에서 제외한다.

| 이벤트 | Slack 알림 |
|---|---|
| Review → Approve | PR 리뷰 승인, 리뷰어, PR 링크 |
| Review → Request changes | PR 수정 요청, 리뷰어, PR 링크 |
| Close (Merge 없음) | PR 거부·종료, 처리자, PR 링크 |
| Merge | PR Merge 완료, 처리자, PR 링크 |

승인 알림은 운영 배포 승인을 검증하거나 실행하지 않는다. 기존 Jenkins의 PR 검증·Dev 테스트·Ops 카나리·복구 절차는 유지한다. 댓글과 이모지 반응에는 알림을 보내지 않는다. PR 제목·리뷰 본문은 메시지에 포함하지 않는다.

## 최초 설정

앱 저장소 chargeops → Settings → Secrets and variables → Actions:

- **Secrets → New repository secret**: `SLACK_CICD_TOKEN` = 기존 Slack Bot OAuth Token (`xoxb-`). Jenkins Credential과 별개로 등록한다. App-Level Token (`xapp-`)은 사용하지 않는다.
- **Variables → New repository variable**: `SLACK_CICD_CHANNEL` = `C0CBS4DFPGW`.
- 봇에 chat:write 권한이 있고 채널에 참여해야 한다. 별도 GitHub PAT는 필요 없다.
- 저장소의 GitHub Actions 사용이 조직 정책상 허용되어 있어야 한다.

## 적용·테스트

1. 알림 파일만 포함한 PR을 검토한 뒤 **dev에 Merge**한다. 이 PR은 앱/Jenkinsfile/GitOps 설정을 바꾸지 않는다. 기존 dev Merge CI가 실행되는지는 기존 Jenkins Job 규칙에 따른다.
2. Actions → **PR Slack notifications** → Run workflow → Branch **dev**로 연결 테스트를 실행한다. 채널에 GitHub Actions Slack 연결 테스트 메시지가 와야 한다. 수동 실행 메뉴는 기본 브랜치에 워크플로가 반영된 뒤 사용한다.
3. 기존 Jenkins 알림 Job `chargeops-pr-notifications`를 잠시 Disable해 중복 발송을 방지한다. 연결 실패 시 기존 Job을 다시 Enable할 수 있다.
4. 새 dev 대상 테스트 PR 또는 Jenkins가 만든 kim_chanju → dev PR에서 다른 사람이 Approve / Request changes를 제출한다. 각각 Slack 알림과 Actions 실행 결과를 확인한다.
5. Merge하지 않고 Close한다. PR 거부·종료 알림을 확인한다. 이 테스트로 Ops 승격이나 Dev 롤백을 실행하지 않는다.
6. Merge 알림 시험은 알림 확인용 변경만 포함한 별도 PR로 진행한다. 기존 dev Merge CI/CD가 연결돼 있다면 운영 승격 조건과 테스트 시간을 먼저 확인한다.
7. 정상 수신 확인 후 기존 Jenkins PR 알림 Job은 Disable 상태로 유지한다. **chargeops-ci와 CI·배포 결과 Slack 알림은 유지**한다. 알림 확인을 위해 이미지 빌드나 Kubernetes Sync를 할 필요는 없다.

`pull_request_target: closed`는 기본 브랜치의 워크플로로 동작한다. 따라서 현재 기본 브랜치인 dev에 반영한 뒤 이후에 발생하는 Close/Merge를 테스트한다. 과거 이벤트를 자동 재생하지 않는다.

이 워크플로에는 checkout, 이미지 빌드, kubectl, PR 승인·Merge API 호출, 정기 polling이 없다. Slack 전송 실패는 Actions에 FAILURE로 남지만 Jenkins CI/CD를 중단하거나 운영을 롤백하지 않는다. 이 워크플로를 배포 승인용 필수 상태 체크로 지정하지 않는다.

## 오류 확인

- Actions에서 실패한 **Send PR decision to Slack** 단계를 확인한다. Secret/Variable 누락, invalid_auth, not_in_channel 등은 토큰 값 없이 출력한다.
- 댓글 리뷰, dev 이외 대상, 외부 fork PR은 알림 대상이 아니므로 건너뛴다.
- HTTP 429/일부 5xx 및 연결 오류는 최대 세 번 시도한다. 재실행 시 동일 알림이 다시 올 수 있으므로 필요한 실패 실행만 재실행한다.
- CI에서 PR 생성까지 성공해도 이 워크플로 실행 결과는 GitHub Actions에서 별도로 확인한다.

## 로컬 검증

```bash
python3 -m unittest discover -s .github/tests -p 'test_*.py' -v
```

테스트는 워크플로 안의 실제 Python 코드를 추출해 합성 이벤트와 모의 HTTP로 실행한다. 실제 Slack 메시지나 배포는 수행하지 않는다.

참고: [GitHub PR 이벤트](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [GitHub Secret 등록](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets), [Slack chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/).
