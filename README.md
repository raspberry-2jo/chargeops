# ChargeOps

전기차 충전 인프라 가동률 관측 · 운영 플랫폼 (라즈베리 2조)

이 README는 **팀원 전원이 깃으로 작업하는 방법**을 정리한 문서임. 깃을 처음 써도 위에서부터 순서대로 따라 하면 됨.

- 처음 한 번만: [1. 처음 시작하기](#1-처음-시작하기-최초-1회)
- 매일 반복: [2. 매일 작업 흐름](#2-매일-작업-흐름)
- 막혔을 때: [6. 이럴 땐 이렇게](#6-이럴-땐-이렇게)
- 단어가 헷갈릴 때: [7. 용어 사전](#7-용어-사전)

질문은 Slack `#dev-git` 채널에 올리면 Git 담당이 답함.

---

## 0. 한눈에 보기

### 브랜치 구조

브랜치(branch, 레포 전체를 복사한 독립 작업 공간)는 3층으로 씀.

```
[ main ]         배포되는 완성본. Git 담당만 마일스톤 때 dev에서 넘김
    ↑ PR
[ dev ]          6명 작업이 모이는 합본. 모두 여기서 시작하고 여기로 PR
    ↑ PR
[ feature/... ]  내 작업 하나를 하는 임시 작업대. 머지되면 삭제
```

| 브랜치 | 내가 직접 커밋? | 내가 하는 일 |
|---|---|---|
| `main` | ❌ | 아무것도 안 함 |
| `dev` | ❌ | 작업 시작할 때 pull 받기, PR 목적지로 지정 |
| 내 작업 브랜치 | ✅ | 여기서만 커밋·푸시 |

### 팀원이 할 일 요약

| 언제 | 할 일 |
|---|---|
| 처음 한 번 | 깃 설치 → 이름·이메일 등록 → 깃허브 초대 수락 → 레포 클론 → 연습 PR |
| 작업할 때마다 | dev pull → 브랜치 생성 → 커밋 → dev 합치기 → 푸시 → PR → 리뷰 반영 → **내가 머지** → 정리 |
| 리뷰 요청 받으면 | 당일 안에 Approve 또는 Request changes |

---

## 1. 처음 시작하기 (최초 1회)

### 1-1. 깃 설치

[git-scm.com](https://git-scm.com)에서 받아 기본값으로 설치함. 같이 깔리는 Git Bash(깃 명령어를 치는 터미널)를 씀.

```bash
# ===== 실행할 명령어 =====
git --version
```

```bash
# ===== 실행 결과 =====
git version 2.51.0.windows.1
```

버전 숫자가 나오면 성공임.

### 1-2. 이름·이메일·기본 설정 등록

커밋(commit, 변경 내용을 이력에 저장)마다 "누가 저장했는지"가 남음. 이메일은 **깃허브 가입 이메일과 같게** 해야 깃허브에서 내 커밋으로 표시됨.

`core.autocrlf`는 윈도우와 리눅스의 줄바꿈 차이 때문에 파일 전체가 바뀐 것처럼 보이는 걸 막는 설정임. `pull.rebase false`는 pull할 때 merge 방식으로 합치라는 설정임.

```bash
# ===== 실행할 명령어 =====
git config --global user.name "홍길동"
git config --global user.email "gildong@example.com"
git config --global core.autocrlf true      # 윈도우만. 맥·리눅스는 input
git config --global pull.rebase false
git config --list
```

```bash
# ===== 실행 결과 =====
user.name=홍길동
user.email=gildong@example.com
core.autocrlf=true
pull.rebase=false
...
```

### 1-3. 깃허브 초대 수락

Git 담당에게 깃허브 아이디를 알려주면 Organization(팀 단위로 레포와 멤버를 관리하는 깃허브 계정)으로 초대가 옴. 메일이나 깃허브 알림에서 **Join**을 눌러야 푸시 권한이 생김.

### 1-4. 레포 클론

클론(clone, 깃허브의 레포를 내 컴퓨터로 통째로 복사)은 처음 한 번만 함.

```bash
# ===== 실행할 명령어 =====
git clone https://github.com/<조직명>/chargeops.git
cd chargeops
git switch dev
git branch
```

```bash
# ===== 실행 결과 =====
Cloning into 'chargeops'...
branch 'dev' set up to track 'origin/dev'.
Switched to a new branch 'dev'
* dev
  main
```

- 처음 클론이나 푸시를 할 때 브라우저 창이 뜨면 깃허브에 로그인하고 **Authorize**를 누름. 한 번 하면 다음부터 자동 로그인됨
- `origin`(깃허브에 있는 원격 레포의 별명): `origin/dev`는 "깃허브의 dev"라는 뜻임
- `*`가 붙은 게 지금 내가 있는 브랜치임

### 1-5. 연습 PR 한 번 해보기

실제 작업 전에 흐름을 한 번 돌려봄. 아래 [2. 매일 작업 흐름](#2-매일-작업-흐름)대로 `docs/practice-내이름` 브랜치를 만들고, `docs/members.md`에 내 이름 한 줄을 추가해서 PR → 승인 → 머지까지 해봄.

---

## 2. 매일 작업 흐름

작업 하나를 할 때마다 ①~⑨를 반복함.

### ① dev 최신으로 받기

어제 다른 사람이 머지한 내용이 내 컴퓨터엔 없을 수 있음. 오래된 dev에서 시작하면 나중에 충돌이 커짐.

```bash
# ===== 실행할 명령어 =====
git switch dev
git pull origin dev
```

- 풀(pull, 깃허브 최신 내용을 내려받아 내 파일에 반영)
- 결과에 `Already up to date.`가 나오면 이미 최신이라는 뜻임

### ② 내 작업 브랜치 만들기

```bash
# ===== 실행할 명령어 =====
git switch -c feature/3.6-collector-api
```

- `switch -c`: 새 브랜치를 만들고(create) 바로 이동함
- 만들었으면 Slack `#dev-git`에 **"3.6 시작 · feature/3.6-collector-api · apps/collector 작업"**처럼 한 줄 남김

브랜치 이름은 `종류/WBS번호-영어-설명`임. 소문자와 `-`만 씀.

| 종류 | 언제 | 예시 |
|---|---|---|
| `feature/` | 새 기능 | `feature/3.1-csms-reset-api` |
| `fix/` | 버그 수정 | `fix/3.6-collector-duplicate` |
| `infra/` | VM·K8s·Terraform·Ansible 설정 | `infra/2.4-k8s-cluster` |
| `docs/` | 문서만 | `docs/1.1-incident-definition` |
| `hotfix/` | main 긴급 수정 (**Git 담당만**) | `hotfix/csms-crash` |

### ③ 작업하고 커밋하기

스테이징(staging, 이번 커밋에 넣을 파일을 골라 올려두기)을 먼저 하고 커밋함. **`git add .`(전부 올리기)는 쓰지 않음.** `.env`나 인증서가 같이 올라갈 수 있음.

```bash
# ===== 실행할 명령어 =====
git status
git add apps/collector/
git commit -m "feat(collector): 공공 API 5분 주기 수집 추가"
```

```bash
# ===== 실행 결과 =====
On branch feature/3.6-collector-api
Changes not staged for commit:
        modified:   apps/collector/main.py
Untracked files:
        apps/collector/scheduler.py

[feature/3.6-collector-api 3f2a1c9] feat(collector): 공공 API 5분 주기 수집 추가
 2 files changed, 84 insertions(+)
```

- `git status`(지금 상태 확인): 어느 브랜치인지, 뭐가 바뀌었는지 보여줌
- `modified`: 기존 파일 수정됨 / `Untracked files`(추적 안 되는 파일): 처음 생긴 새 파일
- `3f2a1c9`: 커밋 해시(commit hash, 커밋마다 붙는 고유 번호)

커밋 메시지는 `타입(범위): 요약` 형식임.

| 타입 | 뜻 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서 |
| `refactor` | 동작은 그대로, 구조만 개선 |
| `test` | 테스트 코드 |
| `chore` | 설정·잡일 |

범위는 폴더 이름: `csms` `vcharger` `collector` `bot` `alert` `ai` `db` `edge` `helm` `monitoring` `infra` `ci` `docs`

### ④ 올리기 전에 dev 최신 내용 합치기

내가 작업하는 동안 다른 사람 PR이 dev에 들어갔을 수 있음. PR 올리기 전에 내 브랜치에 먼저 합치고, **충돌은 내 컴퓨터에서 내가 해결**함.

```bash
# ===== 실행할 명령어 =====
git fetch origin
git merge origin/dev
```

- 페치(fetch, 깃허브 최신 이력을 내려받기만 하고 내 파일은 안 바꿈)
- 머지(merge, 두 브랜치 내용을 하나로 합치기)
- 메시지 편집 창이 뜨면 그대로 저장하고 닫으면 됨

충돌(conflict, 두 사람이 같은 파일의 같은 줄을 다르게 고쳐서 깃이 자동으로 못 합치는 상황)이 나면 이렇게 나옴.

```bash
# ===== 실행 결과 =====
CONFLICT (content): Merge conflict in apps/csms/server.py
Automatic merge failed; fix conflicts and then commit the result.
```

파일을 열면 이런 표시가 있음.

```python
<<<<<<< HEAD
RESET_TIMEOUT = 90        # 내 코드
=======
RESET_TIMEOUT = 120       # dev에 먼저 들어간 코드
>>>>>>> origin/dev
```

1. 남길 코드를 정하고 `<<<<<<<` `=======` `>>>>>>>` 줄을 지움 (VS Code의 Accept Current / Incoming / Both 버튼을 써도 됨)
2. **남의 코드를 지워야 하거나 애매하면 그 코드 쓴 사람한테 먼저 물어봄**
3. 저장하고 다시 커밋함

```bash
# ===== 실행할 명령어 =====
git add apps/csms/server.py
git commit -m "merge: dev 최신 내용 반영"
```

너무 꼬였으면 `git merge --abort`로 합치기 시도 자체를 취소할 수 있음. 내 커밋은 그대로 남음.

### ⑤ 깃허브에 푸시하기

푸시(push, 내 커밋을 깃허브로 올리기)해야 팀원이 보고 PR도 만들 수 있음.

```bash
# ===== 실행할 명령어 =====
git push -u origin feature/3.6-collector-api
```

- `-u`: 업스트림(upstream, 짝으로 연결될 깃허브 브랜치) 설정. 처음 한 번만 붙이고 다음부터는 `git push`만 침
- **main, dev로 직접 푸시는 막혀 있음.** 해도 거부됨

### ⑥ PR 만들기 (작성자 본인)

PR(Pull Request, "내 브랜치를 dev에 합쳐주세요" 요청)은 작업한 본인이 만듦.

1. 레포 페이지의 **Compare & pull request** 클릭
2. **base**(합쳐질 목적지) = `dev` / **compare**(내 브랜치) 확인. base가 `main`이면 반드시 `dev`로 바꿈
3. 제목은 커밋 형식 그대로: `feat(collector): 공공 API 5분 주기 수집`
4. 자동으로 뜨는 PR 템플릿(작성 양식)을 채움. 관련 이슈가 있으면 `Closes #12` 적기
5. Reviewers(리뷰어)는 폴더 담당 팀이 자동으로 들어감. 필요하면 추가
6. 아직 작업 중이면 **Create draft pull request**(초안 PR, 머지가 막힌 상태로 미리 공유)
7. Slack `#dev-git`에 PR 링크 공유

### ⑦ 리뷰 반영하기

수정 요청이 오면 **같은 브랜치에서 고치고 다시 푸시**함. 열려 있는 PR에 자동으로 추가되므로 PR을 새로 만들 필요 없음.

```bash
# ===== 실행할 명령어 =====
git add apps/collector/
git commit -m "fix(collector): 리뷰 반영 - 실패 시 재시도 추가"
git push
```

- 고친 코멘트엔 답글을 달고 **Resolve conversation**(대화 해결 처리)을 누름
- 새 커밋을 푸시하면 기존 승인이 취소됨. 리뷰어에게 다시 봐달라고 요청함

### ⑧ 머지하기 (작성자 본인)

**머지 버튼은 PR 올린 본인이 누름.** 조건은 아래 4개임.

- [ ] 어프루브(Approve, "머지해도 됨" 공식 승인) 2개
- [ ] 그중 1명은 해당 폴더 담당 팀 (CODEOWNERS)
- [ ] 리뷰 코멘트 전부 Resolve
- [ ] 충돌 없음

1. 초록 버튼 옆 화살표에서 **Squash and merge**(스쿼시 머지, 커밋 여러 개를 1개로 압축해서 합치기) 선택
2. 커밋 제목이 PR 제목과 같은지 확인하고 **Confirm squash and merge**
3. **Delete branch** 클릭 (자동 삭제 설정이 켜져 있으면 생략)
4. Slack `#dev-git`에 "3.6 머지 완료" 한 줄. 다른 사람이 dev를 다시 받아야 한다는 신호임

### ⑨ 내 컴퓨터 정리

```bash
# ===== 실행할 명령어 =====
git switch dev
git pull origin dev
git branch -D feature/3.6-collector-api
```

- 스쿼시 머지를 하면 깃이 "아직 안 합친 브랜치"로 착각함. 그래서 소문자 `-d` 대신 대문자 `-D`(강제 삭제)를 씀
- **깃허브에서 머지된 걸 확인한 다음에만** 지움

다음 작업은 다시 ①부터.

---

## 3. 리뷰어가 할 일

리뷰 요청이 오면 **당일 안에** 처리함.

1. PR → **Files changed**(변경된 파일) 탭
2. 의견 있는 줄에서 **+** 눌러 코멘트. 접두어를 붙여서 무게를 알려줌
   - `[필수]` 머지 전에 꼭 고쳐야 함
   - `[제안]` 고치면 좋지만 작성자가 판단
   - `[질문]` 이해가 안 돼서 묻는 것
3. 작은 수정은 **Add a suggestion**(수정 제안) 기능으로 남기면 작성자가 버튼 하나로 반영함
4. **Review changes** → 셋 중 하나 선택 → **Submit review**

| 선택 | 뜻 |
|---|---|
| **Approve** | 승인. 머지해도 됨 (승인 1개로 셈) |
| **Request changes** | 수정 요청. 고칠 때까지 머지 막힘 |
| **Comment** | 의견만 |

**따봉은 반드시 Approve 버튼으로.** 👍 이모지나 "LGTM" 댓글은 승인 수에 안 들어감.

확인할 것:

- [ ] PR 제목·WBS와 내용이 맞고, 범위를 벗어나지 않음
- [ ] 비밀 정보(비밀번호, 키, 토큰, `.pem`·`.key` 파일)가 없음
- [ ] 상관없는 파일이 섞여 있지 않음
- [ ] 공용 파일을 바꿨으면 Slack에 공지했음
- [ ] 테스트 방법이 적혀 있음

하지 않는 것: **남의 PR 브랜치에 직접 푸시하지 않음 / 남의 PR을 대신 머지하지 않음**

---

## 4. 팀 깃 룰

### 브랜치

| # | 룰 | 이유 |
|---|---|---|
| B1 | main, dev에 직접 커밋·푸시 금지. 모든 변경은 PR로 | 리뷰 없는 코드가 배포되거나 남의 작업을 깨는 걸 막음 |
| B2 | 작업 브랜치는 항상 최신 dev에서 땀. 남의 작업 브랜치에서 따지 않음 | 출발점이 어긋나면 충돌이 커짐 |
| B3 | 이름은 `종류/WBS번호-설명`, 소문자와 `-`만 | 목록만 봐도 누가 뭘 하는지 보임 |
| B4 | 브랜치 하나 = 작업 하나. 머지되면 바로 삭제 | 오래 살수록 dev와 멀어짐 |
| B5 | 브랜치 수명은 최대 2일. 넘으면 쪼개서 중간 PR | 작은 PR이 리뷰도 빠르고 충돌도 작음 |
| B6 | 한 브랜치에 두 사람이 같이 푸시하지 않음 | 서로 덮어쓰는 사고가 남 |
| B7 | 머지된 브랜치는 다시 쓰지 않음. 추가 수정은 새 `fix/` 브랜치로 | 이미 압축 머지된 이력과 꼬임 |

### 커밋

| # | 룰 | 이유 |
|---|---|---|
| C1 | 메시지는 `타입(범위): 요약` | dev 이력만 봐도 무슨 변경인지 보임 |
| C2 | `git add .` 금지. 작업한 폴더만 add | 비밀 파일·잡파일 혼입 방지 |
| C3 | 커밋 전에 `git status` 확인 | 엉뚱한 브랜치·파일 커밋 방지 |
| C4 | 포스 푸시(force push, 깃허브 이력을 강제로 덮어쓰기) 금지 | 남의 커밋이 날아갈 수 있음 |
| C5 | 작업 중이어도 **하루 끝에는 푸시** (Draft PR 권장) | 노트북 고장 대비 백업 + 진행 상황 공유 |

### PR · 리뷰 · 머지

| # | 룰 | 이유 |
|---|---|---|
| P1 | PR은 작업자 본인이 만들고 base는 `dev` | 책임 소재를 분명히 함 |
| P2 | PR 올리기 전에 dev를 합치고 충돌은 작성자가 해결 | 리뷰어가 충돌 코드를 볼 수 없음 |
| P3 | 승인 2개, 그중 1명은 폴더 담당 팀 | 영역 전문가 + 다른 시선 |
| P4 | 공동 작업자끼리의 승인은 1개까지만 인정. 1개는 그 작업에 안 들어간 사람에게 받음 | 같이 만든 사람끼리는 같은 실수를 놓침 |
| P5 | 리뷰는 요청받은 당일 안에. 24시간 무응답이면 리뷰어 교체 가능 | 3주 일정에서 리뷰 대기가 제일 큰 병목 |
| P6 | 승인은 Approve 버튼으로만 | 이모지·댓글은 승인 수에 안 들어감 |
| P7 | 리뷰 코멘트엔 `[필수]` `[제안]` `[질문]` 접두어 | 작성자가 뭘 꼭 고쳐야 하는지 바로 앎 |
| P8 | **머지는 작성자 본인이 Squash and merge로.** 브랜치 삭제 + Slack 공지까지 작성자 몫 | 내 작업은 내가 마지막까지 확인하고 넣음 |
| P9 | 리뷰어는 남의 브랜치에 푸시·머지하지 않음 | 작성자가 모르는 변경이 섞임 |
| P10 | PR은 작게. 변경 300줄이 넘으면 쪼갤 수 있는지 먼저 고민 | 큰 PR은 리뷰가 대충 됨 |
| P11 | 의견이 안 맞으면 코멘트로 근거를 남기고, 결론이 안 나면 그 주 Lead가 정함 | 논쟁으로 PR이 멈추지 않게 |

### 충돌 예방

| # | 룰 | 이유 |
|---|---|---|
| K1 | 작업 시작할 때 Slack에 WBS·브랜치명·작업 폴더를 남김 | 누가 어디를 건드리는지 미리 보임 |
| K2 | 남의 폴더 파일을 고칠 땐 **고치기 전에** 담당자에게 알림 | 담당자 작업과 겹치는지 먼저 확인 |
| K3 | 공용 파일(아래 표)은 Slack에 먼저 선언하고, 그 변경만 작은 PR로 따로 올려 바로 머지 | 여러 PR이 같은 파일을 오래 쥐고 있으면 충돌이 쌓임 |
| K4 | 공용 설정 파일은 **내 서비스 블록 안에서만** 수정. 목록형 파일은 맨 아래가 아니라 알파벳 순서로 끼워 넣음 | 모두 맨 아래에 붙이면 매번 같은 줄에서 충돌남 |
| K5 | DB 마이그레이션(migration, 테이블 변경 SQL을 순서대로 쌓는 파일) 이름은 `V날짜시간__설명.sql` | `V003`처럼 번호로 지으면 두 명이 같은 번호를 동시에 만듦 |
| K6 | 매일 아침 내 브랜치에 dev를 합침 (④) | 충돌이 작을 때 풀면 5분, 쌓이면 반나절 |
| K7 | 먼저 머지된 쪽이 기준. 나중 사람이 충돌 해결. 남의 코드를 지울 땐 원작자 확인 | 순서가 정해져 있어야 다툼이 없음 |
| K8 | 공동 작업은 시작할 때 파일 단위로 담당을 나눔 | 같은 파일을 동시에 고칠 일을 줄임 |
| K9 | 내 작업과 상관없는 파일의 줄 정리·들여쓰기는 건드리지 않음 | 의미 없는 변경이 남의 충돌을 만듦 |
| K10 | 파이썬 포맷터는 팀 공통 하나만 사용 | 저장할 때마다 남의 코드 모양이 바뀌는 걸 막음 |

공용 파일 목록:

| 파일 | 왜 겹치나 |
|---|---|
| `deploy/helm/chargeops/values.yaml` | 모든 서비스 배포 설정이 한 파일에 모임 |
| `requirements.txt` · `requirements.lock` | 누구나 라이브러리를 추가함 |
| `db/migrations/` | 테이블 변경이 순서대로 쌓임 |
| `Jenkinsfile` | 모든 서비스의 빌드·배포 단계 |
| `.env.example` · `.gitignore` · `README.md` | 서비스가 늘 때마다 항목이 늘어남 |

### 보안

| # | 룰 | 이유 |
|---|---|---|
| S1 | 키·토큰·인증서·kubeconfig(쿠버네티스 접속 정보)·tfstate(Terraform 상태 파일)·WireGuard 키는 커밋 금지 | 한 번 올라가면 이력에 영원히 남음 |
| S2 | 값이 필요한 설정은 `.env.example`에 **빈 양식만**. 실제 값은 각자 `.env`에 | `.env`는 `.gitignore`로 막혀 있음 |
| S3 | K8s 비밀값은 Sealed Secrets(비밀값을 암호화해서 깃에 올릴 수 있게 해주는 도구)로 암호화한 것만 커밋 | 평문 Secret은 금지 |
| S4 | 실수로 푸시했으면 **즉시 Git 담당에게 알리고 키를 폐기·재발급** | 커밋을 지워도 이미 유출된 것으로 봄 |
| S5 | 라이브러리를 추가하면 버전을 고정해서 적음 | 사람마다 버전이 달라 "내 컴퓨터에선 됨"이 생김 |

### 일정

| # | 룰 | 이유 |
|---|---|---|
| T1 | 매일 아침 dev pull부터 시작 | 하루 출발점을 맞춤 |
| T2 | **마일스톤 전날 18:00 이후엔 dev 머지 금지** (M1 10/15, M2 10/22, M3 10/27, M4 10/29) | Git 담당이 dev를 점검하고 main으로 넘길 시간 |
| T3 | 10/28 기능 동결 이후엔 `fix/`, `docs/` 브랜치만 | 시연 직전 새 기능은 위험함 |
| T4 | 대용량 파일(VM 이미지, 빌드 결과물, 영상, 장비 사진 원본)은 커밋 금지. Notion·드라이브 링크만 | 레포가 무거워지고 클론이 느려짐 |

---

## 5. 폴더 구조와 담당

```
chargeops/
├── apps/
│   ├── csms/               # 3.1, 3.2 관제 서버 · 리셋 API
│   ├── virtual-charger/    # 3.1, 4.3 가상 충전기 · 부하 시험
│   ├── collector/          # 3.6 공공 API 수집기
│   ├── chatops-bot/        # 3.11 Slack 봇 · 승인 버튼
│   ├── alert-router/       # 3.12 Discord · 메일 대체 발송
│   └── ai-gateway/         # 3.13, 3.15 Bedrock · Ollama
├── db/
│   ├── migrations/         # 2.6 DDL (V날짜시간__설명.sql)
│   ├── views/              # 3.8, 3.10 가동률 · 통계 마트 · 이상치 뷰
│   ├── quality/            # 3.9 데이터 품질 점검
│   └── ops/                # 2.7, 2.8, 4.4 복제 · 백업 · 전환
├── edge/
│   ├── esp32-firmware/     # 3.4 모형 충전기
│   └── gateway/            # 3.3 Pi 5 hostapd · nftables · WireGuard
├── deploy/helm/chargeops/  # Helm 차트
├── monitoring/             # 3.7, 3.14 Prometheus 규칙 · Alertmanager · Grafana
├── infra/
│   ├── vagrant/            # 2.4 VM
│   ├── ansible/            # OS · DB 구성
│   └── terraform/          # 2.2, 4.5 AWS · DR
├── docs/                   # 정의서 · 런북 · 보고서
├── Jenkinsfile             # 2.5 CI/CD
└── .github/                # CODEOWNERS · PR 템플릿 (Git 담당 관리)
```

폴더별 리뷰 담당 팀은 `.github/CODEOWNERS`(폴더별 담당 리뷰어를 적어둔 파일)에 있음. PR을 올리면 자동으로 리뷰어로 지정됨.

---

## 6. 이럴 땐 이렇게

| 상황 | 해결 |
|---|---|
| 브랜치 안 만들고 dev에서 작업함 (커밋 전) | `git switch -c feature/...` 하면 수정한 파일이 그대로 따라옴 |
| dev에서 커밋까지 함 (푸시 전) | 아래 [A] |
| 다른 사람과 같은 파일을 동시에 고치는 중 | Slack으로 범위 공유 → 거의 끝난 사람이 먼저 머지 → 다른 사람이 dev 합치고 이어서 작업 |
| 리뷰 기다리는 동안 다음 작업 | dev에서 새 브랜치를 땀. 기존 PR 브랜치에서 이어서 하지 않음 |
| 내 작업이 아직 머지 안 된 남의 코드가 필요함 | 그 PR을 먼저 머지해달라고 요청 → 머지 후 내 브랜치에 dev 합침 |
| 리뷰어가 24시간 넘게 무응답 | Slack 멘션 → 그래도 없으면 같은 담당 팀의 다른 사람으로 교체 |
| 리뷰어 제안을 웹에서 반영했더니 푸시가 `rejected` | `git pull` 후 `git push` |
| dev를 받았더니 실행이 안 됨 | Slack 공유 → 원인 PR 작성자가 30분 안에 `fix/` PR. 오래 걸리면 원인 PR 화면의 **Revert**(해당 변경을 되돌리는 PR 생성) 버튼 |
| 머지 후 버그 발견 | 최신 dev에서 새 `fix/` 브랜치 |
| main(배포본)에서 버그 | Git 담당에게 알림 (hotfix는 Git 담당만) |
| 급하게 다른 브랜치로 가야 함 (커밋은 어중간함) | `git stash`(임시 보관) → 이동 → 돌아와서 `git stash pop`(꺼내기) |
| 비밀 키를 커밋함 (푸시 전) | 아래 [B] |
| 비밀 키를 푸시함 | **즉시 Git 담당에게 알리고 키 폐기·재발급** |
| PR base를 main으로 올림 | PR 제목 옆 **Edit** → base를 `dev`로 |
| 커밋 메시지 오타 (푸시 전) | `git commit --amend -m "새 메시지"` (어멘드, 직전 커밋 고치기) |
| 커밋 메시지 오타 (푸시 후) | 그냥 둠. 스쿼시 머지 때 PR 제목으로 바뀜 |
| 지금 어느 브랜치인지 모르겠음 | `git branch` → `*` 붙은 게 현재 브랜치 |
| 기능 동결 후 새 아이디어 | 깃허브 Issue(할 일 게시판)에 적어두고 구현하지 않음 |

**[A] dev에 커밋해버렸을 때 (푸시 전)**

리셋(reset, 브랜치를 특정 시점으로 되돌리기)을 씀. `--hard`는 변경까지 버리므로 **반드시 첫 줄을 먼저 실행**함.

```bash
# ===== 실행할 명령어 =====
git switch -c feature/3.6-collector-api   # 내 커밋을 새 브랜치에 보존
git switch dev
git reset --hard origin/dev               # dev를 깃허브 상태로 되돌림
git switch feature/3.6-collector-api
```

**[B] 비밀 키 파일을 커밋했을 때 (푸시 전)**

```bash
# ===== 실행할 명령어 =====
git reset --soft HEAD~1      # 직전 커밋 취소, 파일 변경은 유지
git restore --staged .env    # 그 파일만 스테이징에서 빼기
```

그 다음 `.gitignore`에 파일 이름이 있는지 확인하고 다시 커밋함.

---

## 7. 용어 사전

| 용어 | 뜻 |
|---|---|
| 깃(Git) | 파일 변경 이력을 저장·관리하는 프로그램. 내 컴퓨터에서 돌아감 |
| 깃허브(GitHub) | 깃 이력을 인터넷에 올려두고 팀이 같이 쓰는 웹 서비스 |
| 레포(repository) | 프로젝트 파일 전체 + 변경 이력을 담는 저장소 |
| 원격(remote) / 로컬(local) | 깃허브에 있는 레포 / 내 컴퓨터에 있는 레포 |
| origin | 원격 레포의 기본 별명. `origin/dev` = 깃허브의 dev |
| 클론(clone) | 원격 레포를 내 컴퓨터로 통째로 복사 |
| 브랜치(branch) | 레포 전체를 복사한 독립 작업 공간 |
| 스테이징(staging) | 다음 커밋에 넣을 파일을 골라 올려두기 (`git add`) |
| 커밋(commit) | 골라둔 변경을 이력에 저장. 세이브 포인트 |
| 커밋 해시(commit hash) | 커밋마다 붙는 고유 번호 |
| 푸시(push) | 내 커밋을 깃허브로 올리기 |
| 페치(fetch) | 깃허브 최신 이력을 받기만 하고 내 파일은 안 바꿈 |
| 풀(pull) | 깃허브 최신 이력을 받아 내 파일에 반영 (fetch + merge) |
| 머지(merge) | 두 브랜치 내용을 하나로 합치기 |
| 충돌(conflict) | 같은 파일의 같은 줄을 다르게 고쳐서 자동으로 못 합치는 상황 |
| HEAD | 지금 내가 서 있는 브랜치·커밋 |
| 업스트림(upstream) | 내 브랜치와 짝으로 연결된 깃허브 브랜치 |
| PR(Pull Request) | "내 브랜치를 합쳐주세요" 요청. 리뷰가 여기서 이뤄짐 |
| base / compare | PR의 목적지 브랜치 / 내 작업 브랜치 |
| 초안 PR(Draft PR) | 작업 중임을 표시한 PR. 머지가 막힘 |
| 리뷰(review) | 팀원이 변경 코드를 확인하고 의견을 남기는 것 |
| 어프루브(Approve) | "머지해도 됨" 공식 승인 = 따봉 |
| Request changes | 수정 요청. 고칠 때까지 머지 막힘 |
| Resolve conversation | 리뷰 코멘트를 해결 처리 |
| 스쿼시 머지(Squash and merge) | 커밋 여러 개를 1개로 압축해서 합치기. 작업 브랜치 → dev |
| 머지 커밋(Merge commit) | 이력을 그대로 두고 합치기. dev → main |
| 리버트(revert) | 특정 커밋을 거꾸로 되돌리는 새 커밋 만들기 |
| 리셋(reset) | 브랜치를 특정 시점으로 되돌리기 (`--soft` 파일 유지, `--hard` 변경까지 버림) |
| 스태시(stash) | 커밋 안 한 변경을 임시 보관함에 넣기 |
| 어멘드(amend) | 직전 커밋 고치기 (푸시 전만) |
| 포스 푸시(force push) | 깃허브 이력을 강제로 덮어쓰기. 금지 |
| 태그(tag) | 특정 커밋에 붙이는 버전 이름표 (`v1.0.0`) |
| 마일스톤(milestone) | 중간 목표 시점. 우리는 M1~M4 |
| 핫픽스(hotfix) | 배포본(main) 긴급 수정 |
| 브랜치 보호 규칙(Ruleset) | 직접 푸시 금지·승인 2개 같은 브랜치 제한 설정 |
| CODEOWNERS | 폴더별 담당 리뷰어 파일. PR이 오면 자동 지정 |
| .gitignore | 깃이 무시할 파일 목록 |
| .gitattributes | 파일 종류별 깃 처리 방식(줄바꿈 등)을 정하는 파일 |
| 마이그레이션(migration) | DB 테이블 변경 SQL을 순서대로 쌓는 파일 |
| 이슈(Issue) | 깃허브의 할 일·버그 게시판 |
| Organization | 팀 단위로 레포와 멤버를 관리하는 깃허브 계정 |
