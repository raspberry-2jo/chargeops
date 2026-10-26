# 코드 변경 없이 chargeops-ci가 반복 실행될 때

CI는 앱 저장소 kim_chanju 또는 dev 변경에 반응한다. PR이 승인되지 않았다는 이유로 CI를 계속 실행하지 않는다. 승인 대기는 GitHub PR에서 처리한다.

기존 Jenkinsfile은 Dev 배포 단계에서 GitOps main을 추가 checkout하면서 poll=false를 지정하지 않았다. Jenkins는 기본적으로 여러 checkout을 SCM 감시에 포함한다. CI가 GitOps에 이미지 Digest를 Push하면 그 변경이 다음 CI를 유발하고, 다음 빌드도 새 태그와 Digest를 발행해 다시 GitOps에 Push할 수 있다.

수정 후 GitOps checkout은 poll=false, changelog=false로 지정한다. checkout과 GitOps Push는 유지하고, CI 재실행 감시에서는 제외한다. 앱 저장소 checkout과 H/5 SCM polling은 유지한다. 근거: [Jenkins SCM Step 문서](https://plugins.jenkins.io/workflow-scm-step/).

적용 확인:

1. 수정된 Jenkinsfile을 읽는 빌드를 한 번 완료한다. kim_chanju Job에서는 Ops 승격이 실행되지 않는다.
2. 기존에 예약된 대기 항목이 있다면 해당 항목만 취소한다. 실행 중인 Ops 카나리·복구 Job은 이 이유로 중단하지 않는다.
3. 앱 저장소에 추가 Push 없이 두 번의 polling 주기(약 10분)를 확인한다.
4. Jenkins chargeops-ci의 Polling Log에서 앱 저장소를 검사하고, GitOps main 변경이 새 빌드의 원인으로 잡히지 않는지 확인한다.
5. 앱의 새 커밋을 Push했을 때는 CI가 자동 시작해야 한다.

계속 반복된다면 Polling Log와 Build Triggers를 확인한다. chargeops-ci에 Build periodically가 켜져 있으면 코드 변경 여부와 무관하게 실행될 수 있다. GitHub Webhook과 polling을 함께 사용 중이면 별도 중복 요청도 확인한다. 실제 서버의 반복 원인은 Polling Log로 확정한다.

chargeops-pr-notifications는 1분 간격으로 돌아야 하는 별도 관찰 Job이다. 이 Job의 정상 실행 원인은 Started by timer이며 이미지 빌드나 Dev 배포를 수행하지 않는다. 두 Job을 혼동하지 않는다.
