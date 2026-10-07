# ChargeOps

전기차 충전 인프라 가동률 관측 · 운영 플랫폼 (라즈베리 2조)

이 README는 **팀원 전원이 깃으로 작업하는 방법**을 정리한 문서임. 깃을 처음 써도 위에서부터 순서대로 따라 하면 됨.

- 처음 한 번만: [1. 처음 시작하기](#1-처음-시작하기-최초-1회)
- 매일 반복: [2. 작업 흐름](#2-작업-흐름)
- 막혔을 때: [6. 이럴 땐 이렇게](#6-이럴-땐-이렇게)
- 단어가 헷갈릴 때: [7. 용어 사전](#7-용어-사전)


---

## 0. 한눈에 보기

### 브랜치 구조

브랜치(branch, 레포 전체를 복사한 독립 작업 공간)는 3층으로 씀.

```
[ main ]         배포되는 완성본. Git 담당만 마일스톤 때 dev에서 넘김
    ↑ PR
[ dev ]          6명 작업이 모이는 합본. 모두 여기서 시작하고 여기로 PR
    ↑ PR
[ 이름 브랜치 ]    각자 하나씩. 3주 동안 계속 씀 (지우지 않음)
```

| 팀원 | 브랜치 이름 |
|---|---|
| 윤하빈 | `yoon_habeen` |
| 성주원 | `sung_juwon` |
| 황채연 | `hwang_chaeyeon` |
| 김찬주 | `kim_chanju` |
| 조인성 | `cho_inseong` |
| 강영수 | `kang_youngsoo` |

| 브랜치 | 내가 직접 커밋? | 내가 하는 일 |
|---|---|---|
| `main` | ❌ | 아무것도 안 함 |
| `dev` | ❌ | 작업 시작할 때 pull 받기, PR 목적지로 지정 |
| 내 이름 브랜치 | ✅ | 여기서만 커밋·푸시. 남의 이름 브랜치는 건드리지 않음 |

### 팀원이 할 일 요약

| 언제 | 할 일 |
|---|---|
| 처음 한 번 | 깃허브 초대 수락 → 깃 설치 → 이름·이메일 등록 → 레포 클론 → **내 이름 브랜치로 이동** → 잘 됐는지 확인 → 연습 PR |
| 매일 아침 | 내 브랜치에 dev 최신 내용 합치기 |
| 작업 하나 끝날 때마다 | 커밋 → dev 합치기 → 푸시 → PR → 리뷰 반영 → **내가 머지** → 다시 dev 합치기 |
| 리뷰 요청 받으면 | 당일 안에 Approve 또는 Request changes |

---

## 1. 처음 시작하기 (최초 1회)

**브랜치와 폴더는 Git 담당이 이미 다 만들어 둠.** 팀원은 새로 만들 것 없이 받아서(클론) 내 브랜치로 이동만 하면 됨. 아래 1-1 ~ 1-7을 순서대로 하고, 막히면 화면을 캡처해서 팀 슬랙에 올림.

### 1-1. 깃허브 초대 수락

Git 담당이 Organization(팀 단위로 레포와 멤버를 관리하는 깃허브 계정) **raspberry-2jo**로 초대를 보냄. 수락해야 푸시 권한이 생김.

- 깃허브에 로그인한 상태로 https://github.com/orgs/raspberry-2jo/invitation 접속 → **Join raspberry-2jo**
- 또는 메일함(스팸함 포함)의 초대 메일에서 **Join @raspberry-2jo**
- 2단계 인증 설정 화면이 나오면 그대로 따라서 설정함

### 1-2. 깃 설치

[git-scm.com](https://git-scm.com) → **Download for Windows** → 기본값으로 설치함. 같이 깔리는 **Git Bash**(깃 명령어를 치는 터미널)를 시작 메뉴에서 열고, 이후 명령어는 전부 여기에 입력함.

```bash
# ===== 실행할 명령어 =====
git --version
```

```bash
# ===== 실행 결과 =====
git version 2.5x.x.windows.1
```

버전 숫자가 나오면 성공임.

### 1-3. 이름·이메일·기본 설정 등록

커밋(commit, 변경 내용을 이력에 저장)마다 "누가 저장했는지"가 남음. 우리 레포는 공개라서 이 이메일이 누구에게나 보임. 그래서 진짜 이메일 대신 **깃허브가 주는 noreply 주소**를 씀.

1. 깃허브 → 오른쪽 위 프로필 → **Settings** → **Emails**
2. **Keep my email address private** 체크 (아래 **Block command line pushes that expose my email**도 체크 추천)
3. 그 아래 나오는 `숫자+아이디@users.noreply.github.com` 주소를 복사해서 아래 `user.email`에 넣음

`user.name`은 커밋에 찍히는 작성자 이름임. 브랜치 이름(`sung_juwon`)과는 별개로, **각자 자기 컴퓨터에서 직접** 설정해야 함. 팀 규칙은 **영문 성+이름 붙여 쓰기**(예: `yoonhabeen`, `sungjuwon`).

`--global`이 붙어 있어서 어느 폴더에서 실행하든 이 컴퓨터 전체에 적용됨. Git Bash를 열자마자 바로 실행하면 됨.

```bash
# ===== 실행할 명령어 =====
git config --global user.name "sungjuwon"      # 본인 이름 영문 (성+이름 붙여서, 브랜치 이름과 별개)
git config --global user.email "12345678+gildong@users.noreply.github.com"
git config --global core.autocrlf true      # 윈도우용 줄바꿈 설정. 리눅스는 input
git config --global pull.rebase false       # pull할 때 merge 방식으로 합침
```

### 1-4. 레포 클론

클론(clone, 깃허브의 레포를 내 컴퓨터로 통째로 복사)은 처음 한 번만 함. 경로에 한글 · 띄어쓰기가 없는 폴더에서 하는 게 안전함 (Git Bash에서 D드라이브는 `/d/`).

```bash
# ===== 실행할 명령어 =====
cd /d/
mkdir -p work && cd work
git clone https://github.com/raspberry-2jo/chargeops.git
cd chargeops
```

```bash
# ===== 실행 결과 =====
Cloning into 'chargeops'...
```

- 브라우저 로그인 창이 뜨면 **본인 깃허브 계정**으로 로그인하고 **Authorize**를 누름. 한 번 하면 다음부터 자동 로그인됨
- 클론 직후엔 `dev` 브랜치에 있음. **dev에서는 작업하지 않음** (푸시가 막혀 있음). 바로 1-5로 감
- `origin`(깃허브에 있는 원격 레포의 별명): `origin/dev`는 "깃허브의 dev"라는 뜻임

### 1-5. 내 이름 브랜치로 이동

내 이름 브랜치는 **Git 담당이 이미 만들어 둠.** 새로 만들지 말고 이동만 함. 이름은 [0. 한눈에 보기](#0-한눈에-보기)의 표대로 씀.

```bash
# ===== 실행할 명령어 =====
git switch sung_juwon        # 각자 자기 이름
```

```bash
# ===== 실행 결과 =====
branch 'sung_juwon' set up to track 'origin/sung_juwon'.
Switched to a new branch 'sung_juwon'
```

- `git switch 이름`: 깃허브에 같은 이름 브랜치가 있으면 자동으로 받아와서 짝(업스트림, upstream)으로 연결해줌. 이후로는 `git push`만 치면 됨
- **`git switch -c`(새로 만들기)는 쓰지 않음.** 이미 있는 브랜치와 이름이 겹쳐서 에러남
- 명령어를 치기 전에 프롬프트 끝의 괄호가 **(내 이름)**인지 항상 확인하는 습관을 들임

### 1-6. 잘 됐는지 확인

아래 4가지가 다 맞으면 세팅 끝임. 하나라도 다르면 결과를 캡처해서 팀 슬랙에 올림.

```bash
# ===== 실행할 명령어 =====
git config --global user.name
git config --global user.email
git branch -vv
ls docs/setup
```

```bash
# ===== 실행 결과 =====
sungjuwon
12345678+gildong@users.noreply.github.com
  dev        fca3bee [origin/dev] Merge pull request #1 ...
* sung_juwon fca3bee [origin/sung_juwon] Merge pull request #1 ...
README.md  _template.md  versions.md
```

| 확인할 것 | 정상 |
|---|---|
| 이름 · 이메일 | 영문 성+이름, noreply 주소 |
| `*`가 붙은 브랜치 | **내 이름 브랜치** |
| 내 브랜치 옆 괄호 | `[origin/내이름]` (깃허브 브랜치와 연결됨) |
| `ls docs/setup` | 파일 3개가 보임 (최신 내용을 받은 것) |

### 1-7. 연습 PR 한 번 해보기

실제 작업 전에 커밋 → 푸시 → PR → 승인 → 머지 흐름을 한 번 돌려봄. `docs/members.md`에 내 이름 한 줄을 추가함.

```bash
# ===== 실행할 명령어 =====
echo "- 성주원 (sung_juwon)" >> docs/members.md
git status
git add docs/members.md
git commit -m "docs: 팀원 이름 추가"
git push
```

푸시 다음은 깃허브에서 함. 자세한 화면 설명은 [2. 작업 흐름](#2-작업-흐름)의 ⑥ ~ ⑨에 있음.

1. 레포 화면 노란 배너의 **Compare & pull request** 클릭
2. **base: dev ← compare: 내 이름**인지 확인
3. 오른쪽 **Reviewers**에 2명 지정 → **Create pull request**
4. 승인 2개가 모이면 **본인이** **Create a merge commit** → **Confirm merge** (**Delete branch는 누르지 않음**)
5. 머지 후 Git Bash에서 다시 dev 최신 내용 합치기

```bash
# ===== 실행할 명령어 =====
git fetch origin
git merge origin/dev
git push
```

- 6명이 같은 파일 끝에 한 줄씩 추가하는 거라서 **두 번째 사람부터 충돌이 날 수 있음.** 그러면 PR 화면에 `This branch has conflicts`가 뜸. 위 3줄을 먼저 실행하고, VS Code에서 `docs/members.md`를 열어 **Accept Both Changes**(두 줄 다 남기기) → 저장 → `git add docs/members.md` → `git commit -m "merge: dev 합치기"` → `git push` 하면 PR이 자동으로 갱신됨
- 충돌 해결을 한 번 겪어보는 게 이 연습의 목적이기도 함
- 다른 사람 PR의 리뷰 요청이 오면 [3. 리뷰어가 할 일](#3-리뷰어가-할-일)대로 승인함

---

## 2. 작업 흐름

모든 작업은 **내 이름 브랜치 하나**에서 함. 브랜치는 3주 내내 그대로 쓰고, **PR은 작업 하나가 끝날 때마다** 올림. 3주치를 모아서 한 번에 올리면 리뷰도 못 하고 충돌도 폭발하기 때문임.

### ① 매일 아침: 내 브랜치에 dev 최신 내용 합치기

어제 다른 사람이 머지한 내용이 내 브랜치엔 아직 없음. 매일 아침 받아서 합쳐야 내 브랜치와 dev의 차이가 쌓이지 않음.

```bash
# ===== 실행할 명령어 =====
git switch yoon_habeen
git fetch origin
git merge origin/dev
```

```bash
# ===== 실행 결과 =====
Already up to date.
```

- 페치(fetch, 깃허브 최신 이력을 내려받기만 하고 내 파일은 안 바꿈)
- 머지(merge, 두 브랜치 내용을 하나로 합치기): 깃허브의 dev를 지금 내 브랜치에 합침
- `Already up to date.`면 받을 게 없다는 뜻임. 메시지 편집 창이 뜨면 그대로 저장하고 닫으면 됨
- 충돌이 나면 ④의 "충돌이 났을 때"를 보고 해결함

### ② 작업 시작 알리기

팀 슬랙에 **"3.6 시작 · yoon_habeen · apps/collector 작업"**처럼 한 줄 남김. 누가 어느 폴더를 건드리는지 미리 보여서 충돌을 줄임.

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
On branch yoon_habeen
Changes not staged for commit:
        modified:   apps/collector/main.py
Untracked files:
        apps/collector/scheduler.py

[yoon_habeen 3f2a1c9] feat(collector): 공공 API 5분 주기 수집 추가
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
git push
```

- 1-5에서 `git switch 내이름`으로 깃허브 브랜치와 연결해뒀으니 `git push`만 치면 내 이름 브랜치로 올라감
- **main, dev로 직접 푸시는 막혀 있음.** 해도 거부됨

### ⑥ PR 만들기 (작성자 본인)

PR(Pull Request, "내 브랜치를 dev에 합쳐주세요" 요청)은 작업한 본인이 만듦.

1. 레포 페이지의 **Compare & pull request** 클릭
2. **base**(합쳐질 목적지) = `dev` / **compare**(내 브랜치) = `yoon_habeen` 확인. base가 `main`이면 반드시 `dev`로 바꿈
3. 제목은 커밋 형식 그대로: `feat(collector): 공공 API 5분 주기 수집`
4. 자동으로 뜨는 PR 템플릿(작성 양식)을 채움. 관련 이슈가 있으면 `Closes #12` 적기
5. 오른쪽 **Reviewers**(리뷰어)에 내 작업과 맞닿은 사람 2명 이상을 직접 지정
6. 아직 작업 중이면 **Create draft pull request**(초안 PR, 머지가 막힌 상태로 미리 공유)
7. 팀 슬랙에 PR 링크 공유

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

**리뷰 기다리는 동안 다음 작업은?** PR이 열려 있는 동안 내 브랜치에 푸시하면 **그 PR에 같이 들어감.** 그래서 리뷰 대기 중에는 다음 작업을 하되 **커밋·푸시는 하지 않음.** 그 사이 수정 요청이 오면 스태시(stash, 커밋 안 한 변경을 임시 보관함에 넣기)로 잠깐 치워두고 고침.

```bash
# ===== 실행할 명령어 =====
git stash                     # 하던 다음 작업을 임시 보관
# (리뷰 수정 → add → commit → push)
git stash pop                 # 하던 작업 다시 꺼내기
```

### ⑧ 머지하기 (작성자 본인)

**머지 버튼은 PR 올린 본인이 누름.** 조건은 아래 4개임.

- [ ] 어프루브(Approve, "머지해도 됨" 공식 승인) 2개
- [ ] 2명 모두 내 작업과 맞닿은 사람
- [ ] 리뷰 코멘트 전부 Resolve
- [ ] 충돌 없음

1. 초록 버튼 **Merge pull request**를 누름. 머지 방식은 **Create a merge commit**(내 커밋을 그대로 보존하며 합치기)임
2. **Confirm merge** 클릭
3. **Delete branch 버튼이 떠도 누르지 않음.** 내 이름 브랜치는 계속 씀
4. 팀 슬랙에 "3.6 머지 완료" 한 줄. 다른 사람이 dev를 다시 받아야 한다는 신호임

**왜 스쿼시(커밋을 1개로 압축)가 아니라 머지 커밋인가?** 스쿼시로 합치면 dev에 들어간 압축본과 내 브랜치의 원래 커밋이 깃 입장에서 "다른 커밋"이 됨. 같은 브랜치에서 다음 PR을 올릴 때 이미 합친 내용이 또 올라오거나 충돌이 남. 이름 브랜치를 계속 쓰려면 머지 커밋이어야 함.

### ⑨ 머지 직후: 다시 dev 합치기

방금 머지된 내 작업과, 그 사이 들어온 다른 사람 작업까지 내 브랜치에 받아둠. 그래야 다음 작업이 최신 기준에서 시작됨.

```bash
# ===== 실행할 명령어 =====
git switch yoon_habeen
git fetch origin
git merge origin/dev
git push
```

다음 작업은 다시 ②부터.

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
| B2 | 각자 **내 이름 브랜치 하나**에서만 작업. 이름 브랜치는 지우지 않음 | 누가 어디서 작업하는지 단순하게 |
| B3 | **남의 이름 브랜치에는 절대 푸시하지 않음** | 이름 브랜치는 잠금 규칙이 없어서 실수로 덮어쓸 수 있음 |
| B4 | **매일 아침, 그리고 PR이 머지된 직후** 내 브랜치에 dev를 합침 | 내 브랜치가 dev와 멀어질수록 충돌이 커짐 |
| B5 | 브랜치는 3주 내내 쓰지만 **PR은 작업 하나 끝날 때마다**. 2일 넘게 PR을 안 올리고 쌓아두지 않음 | 작은 PR이 리뷰도 빠르고 충돌도 작음 |
| B6 | 공동 작업도 각자 자기 이름 브랜치에서. 파일을 나눠서 작업 | 한 브랜치에 두 사람이 푸시하면 서로 덮어씀 |
| B7 | PR이 열려 있는 동안은 다음 작업을 커밋·푸시하지 않음 | 푸시하면 리뷰 중인 PR에 같이 섞여 들어감 |

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
| P3 | **승인 2개는 내 작업과 맞닿은 사람에게 받음** (5번 폴더표 참고) | 내 코드와 이어진 부분을 아는 사람이 봐야 실제로 문제를 잡음 |
| P4 | 맞닿은 사람이 하루 넘게 응답이 없으면 Slack 공지 후 다른 사람 승인 1개로 대신할 수 있음 | 리뷰 대기로 일정이 멈추지 않게 |
| P5 | 리뷰는 요청받은 당일 안에. 24시간 무응답이면 리뷰어 교체 가능 | 3주 일정에서 리뷰 대기가 제일 큰 병목 |
| P6 | 승인은 Approve 버튼으로만 | 이모지·댓글은 승인 수에 안 들어감 |
| P7 | 리뷰 코멘트엔 `[필수]` `[제안]` `[질문]` 접두어 | 작성자가 뭘 꼭 고쳐야 하는지 바로 앎 |
| P8 | **머지는 작성자 본인이 Create a merge commit으로.** 브랜치는 지우지 않고, Slack 공지 + 머지 후 dev 합치기까지 작성자 몫 | 내 작업은 내가 마지막까지 확인하고 넣음 |
| P9 | 리뷰어는 남의 브랜치에 푸시·머지하지 않음 | 작성자가 모르는 변경이 섞임 |
| P10 | PR은 작게. 변경 300줄이 넘으면 쪼갤 수 있는지 먼저 고민 | 큰 PR은 리뷰가 대충 됨 |
| P11 | 의견이 안 맞으면 코멘트로 근거를 남기고, 결론이 안 나면 그 주 Lead가 정함 | 논쟁으로 PR이 멈추지 않게 |

### 충돌 예방

| # | 룰 | 이유 |
|---|---|---|
| K1 | 작업 시작할 때 Slack에 WBS·브랜치명·작업 폴더를 남김 | 누가 어디를 건드리는지 미리 보임 |
| K2 | 남의 폴더 파일을 고칠 땐 **고치기 전에** 그 폴더 작업자(5번 표)에게 알림 | 작업자 작업과 겹치는지 먼저 확인 |
| K3 | 공용 파일(아래 표)은 Slack에 먼저 선언하고, 그 변경만 작은 PR로 따로 올려 바로 머지 | 여러 PR이 같은 파일을 오래 쥐고 있으면 충돌이 쌓임 |
| K4 | 공용 설정 파일은 **내 서비스 블록 안에서만** 수정. 목록형 파일은 맨 아래가 아니라 알파벳 순서로 끼워 넣음 | 모두 맨 아래에 붙이면 매번 같은 줄에서 충돌남 |
| K5 | DB 마이그레이션(migration, 테이블 변경 SQL을 순서대로 쌓는 파일) 이름은 `V날짜시간__설명.sql` | `V003`처럼 번호로 지으면 두 명이 같은 번호를 동시에 만듦 |
| K6 | 매일 아침 내 브랜치에 dev를 합침 (①) | 충돌이 작을 때 풀면 5분, 쌓이면 반나절 |
| K7 | 먼저 머지된 쪽이 기준. 나중 사람이 충돌 해결. 남의 코드를 지울 땐 원작자 확인 | 순서가 정해져 있어야 다툼이 없음 |
| K8 | 공동 작업은 시작할 때 파일 단위로 담당을 나눔 | 같은 파일을 동시에 고칠 일을 줄임 |
| K9 | 내 작업과 상관없는 파일의 줄 정리·들여쓰기는 건드리지 않음 | 의미 없는 변경이 남의 충돌을 만듦 |
| K10 | 파이썬 포맷터(formatter, 코드 모양을 자동 정리하는 도구)는 팀 공통 하나만 사용. **정해지기 전까진 저장 시 자동 포맷을 꺼둠** | 저장할 때마다 남의 코드 모양이 바뀌는 걸 막음 |

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
| S2-1 | 비밀값이 들어가는 설정 파일(hostapd.conf, ESP32 `secrets.h`, pgbackrest.conf, tfvars, 인벤토리 등)은 **값을 지운 `파일명.example`만 커밋**함. 진짜 파일은 `.gitignore`가 막음 | 다른 팀원이 양식을 보고 자기 값을 채워 쓸 수 있음 |
| S2-2 | 새 종류의 비밀 파일이 생기면 **커밋 전에** `.gitignore`에 추가하는 PR부터 올림 | `.gitignore`는 이미 커밋된 파일은 못 막음 |
| S3 | K8s 비밀값은 Sealed Secrets(비밀값을 암호화해서 깃에 올릴 수 있게 해주는 도구)로 암호화한 것만 커밋 | 평문 Secret은 금지 |
| S4 | 실수로 푸시했으면 **즉시 Git 담당에게 알리고 키를 폐기·재발급** | 커밋을 지워도 이미 유출된 것으로 봄 |
| S5 | 라이브러리를 추가하면 버전을 고정해서 적음 | 사람마다 버전이 달라 "내 컴퓨터에선 됨"이 생김 |
| S6 | **코드 안에 키·비밀번호를 직접 적지 않음.** 항상 `os.environ["DB_PASSWORD"]`처럼 환경변수로 읽음 | `.gitignore`는 "파일"만 막음. `.py` 안에 적힌 키는 그대로 올라감 |
| S7 | 웹훅 URL(Slack·Discord), 실제 공인 IP, 개인 전화번호·이메일도 비밀 정보로 취급 | 공개 레포라 누구나 볼 수 있음. 웹훅 URL만 있으면 아무나 우리 채널에 메시지를 보냄 |
| S8 | 스크린샷·로그·문서를 올릴 땐 토큰·키·비밀번호가 찍혀 있지 않은지 확인 | 터미널 캡처에 토큰이 찍혀 유출되는 경우가 많음 |
| S9 | 깃허브 **Settings → Emails → Keep my email address private** 켜고, 깃 이메일을 깃허브가 주는 `noreply` 주소로 설정 | 공개 레포에선 커밋마다 내 이메일이 공개됨 |

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
│   ├── csms/               # 관제 서버 · 리셋 API
│   ├── virtual-charger/    # 가상 충전기 · 부하 시험
│   ├── collector/          # 공공 API 수집기
│   ├── chatops-bot/        # Slack 봇 · 승인 버튼
│   ├── alert-router/       # Discord · 메일 대체 발송
│   └── ai-gateway/         # Bedrock · Ollama
├── db/
│   ├── migrations/         # DDL (V날짜시간__설명.sql)
│   ├── views/              # 가동률 · 통계 마트 · 이상치 뷰
│   ├── quality/            # 데이터 품질 점검
│   └── ops/                # 복제 · 백업 · 전환 스크립트
├── edge/
│   ├── esp32-firmware/     # 모형 충전기
│   └── gateway/            # Pi 5 hostapd · nftables · WireGuard
├── deploy/helm/chargeops/  # Helm 차트
├── monitoring/
│   ├── prometheus-rules/   # 장애 감지 규칙
│   ├── alertmanager/       # 등급 · 묶음 · 억제
│   └── grafana/            # 대시보드
├── infra/
│   ├── vagrant/            # VM
│   ├── ansible/            # OS · DB 구성 자동화
│   └── terraform/          # AWS · DR
├── docs/                   # 정의서 · 런북 · 보고서
├── Jenkinsfile             # CI/CD
└── .github/                # PR 템플릿 (Git 담당 관리)
```

### 폴더별 작업자와 역할

착수보고서 WBS 배분 기준임. 내 작업이 들어갈 폴더를 여기서 확인함.

| 폴더 | 작업자 | 역할 | WBS |
|---|---|---|---|
| `apps/csms/` | 성주원 · 윤하빈 | 충전기 연결 관리, Soft·Hard 리셋 API, 충전 중 차단·횟수 제한·멱등 | 3.1, 3.2 |
| `apps/virtual-charger/` | 성주원 · 윤하빈 | 가상 충전기, 1,000대 부하 시험 | 3.1, 4.3 |
| `apps/collector/` | 강영수 | 공공 API 5분 주기 수집, 변경분 적재, 원본 S3 보관 | 3.6 |
| `apps/chatops-bot/` | 김찬주 · 황채연 | Slack 장애 스레드, 승인 버튼, 권한 판정·감사 기록 | 3.11 |
| `apps/alert-router/` | 김찬주 · 황채연 | Discord·메일 대체 발송, Watchdog | 3.12 |
| `apps/ai-gateway/` | 김찬주 | Bedrock 장애 요약, pgvector 유사 사례, Ollama 폴백 | 3.13, 3.15 |
| `db/migrations/` | 조인성 · 강영수 · 윤하빈 | ERD, DDL, 월 파티션, 역할 분리 | 1.4, 2.6 |
| | 김찬주 | ops_role 등 권한 테이블 설계 | 1.5 |
| `db/views/` | 성주원 · 윤하빈 | 가동률·MTTR 집계, 운영사별 통계 마트 | 3.8 |
| | 조인성 · 강영수 | 이상치 점수·등급 SQL 뷰 | 3.10 |
| `db/quality/` | 조인성 | 누락·중복·이상값 품질 점검 | 3.9 |
| `db/ops/` | 조인성 · 강영수 · 윤하빈 | Standby 복제, PgBouncer, pgBackRest·PITR, 장애 전환 훈련, 쿼리 튜닝 | 2.7, 2.8, 4.4 |
| `edge/gateway/` | 성주원 · 윤하빈 | Pi 5 AP, 충전기망 분리, WireGuard 터널 | 3.3 |
| | 성주원 | 망 분리, 사설 CA, mTLS | 3.5 |
| `edge/esp32-firmware/` | 성주원 · 윤하빈 | ESP32 OCPP 연동, 온도·전류 센서 | 3.4 |
| `monitoring/prometheus-rules/`, `alertmanager/` | 조인성 | 장애 유형별 감지 규칙, SEV 등급, 묶음·억제 | 3.7 |
| `monitoring/grafana/` | 조인성 · 강영수 · 윤하빈 | 가동률·DB·부하 대시보드 | 3.14 |
| `infra/vagrant/`, `infra/ansible/` | 황채연 · 성주원 · 김찬주 | 실습 LAN, VM, K8s 클러스터 구축 | 2.4 |
| `infra/terraform/` | 성주원 · 황채연 | AWS 계정·IAM·Budgets·Bedrock 접근 | 2.2 |
| | 전원 | DR 훈련 환경 | 4.5 |
| `deploy/helm/chargeops/` | 황채연 · 성주원 · 김찬주 | 차트 뼈대·공통 설정 (각 서비스 작업자는 자기 서비스 블록만 수정) | 2.4~ |
| `Jenkinsfile` | 김찬주 · 황채연 | CI/CD 파이프라인 | 2.5 |
| `docs/` | 전원 | 요구사항·장애 정의서, 아키텍처, 런북·보고서 | 1.1~1.3, 5.1 |
| `.github/`, `README.md` | 윤하빈 (Git 담당) | 깃 규칙·PR 템플릿 관리 | 2.1 |

### 승인은 누구에게 받나

승인 2개는 **내 작업과 맞닿은 사람**에게 받음. 같은 폴더를 같이 작업하는 사람, 내 코드를 호출하거나 내 결과를 받아 쓰는 사람을 PR의 Reviewers에 직접 지정함. 예를 들어 리셋 API를 바꿨으면 같이 작업한 사람과 그 API를 호출하는 봇 작업자에게 받음.

---

## 6. 이럴 땐 이렇게

| 상황 | 해결 |
|---|---|
| 내 브랜치가 아니라 dev에서 작업함 (커밋 전) | `git stash` → `git switch yoon_habeen` → `git stash pop` 하면 수정한 내용이 내 브랜치로 옮겨짐 |
| dev에서 커밋까지 함 (푸시 전) | 아래 [A] |
| 다른 사람과 같은 파일을 동시에 고치는 중 | Slack으로 범위 공유 → 거의 끝난 사람이 먼저 머지 → 다른 사람이 dev 합치고 이어서 작업 |
| 리뷰 기다리는 동안 다음 작업 | 작업은 하되 커밋·푸시는 머지 후에. 수정 요청이 오면 `git stash`로 치워두고 고침 (⑦) |
| 내 작업이 아직 머지 안 된 남의 코드가 필요함 | 그 PR을 먼저 머지해달라고 요청 → 머지 후 내 브랜치에 dev 합침 |
| 리뷰어가 24시간 넘게 무응답 | Slack 멘션 → 맞닿은 다른 사람에게 요청 → 그래도 없으면 Slack 공지 후 다른 사람 1명 (룰 P4) |
| 리뷰어 제안을 웹에서 반영했더니 푸시가 `rejected` | `git pull` 후 `git push` |
| dev를 받았더니 실행이 안 됨 | Slack 공유 → 원인 PR 작성자가 30분 안에 `fix/` PR. 오래 걸리면 원인 PR 화면의 **Revert**(해당 변경을 되돌리는 PR 생성) 버튼 |
| 머지 후 버그 발견 | 내 브랜치에 dev 합치고 → 고쳐서 새 PR (`fix(범위): …`) |
| main(배포본)에서 버그 | Git 담당에게 알림 (hotfix는 Git 담당만) |
| 급하게 다른 브랜치로 가야 함 (커밋은 어중간함) | `git stash`(임시 보관) → 이동 → 돌아와서 `git stash pop`(꺼내기) |
| 비밀 키를 커밋함 (푸시 전) | 아래 [B] |
| 비밀 키를 푸시함 | **즉시 Git 담당에게 알리고 키 폐기·재발급** |
| 내 파일이 `.gitignore`에 걸려서 `git add`가 안 됨 | 비밀 파일이면 정상임. 양식이 필요하면 값을 지우고 `파일명.example`로 저장해서 올림. 비밀이 아닌데 막혔으면 Git 담당에게 알림 |
| 이미 커밋한 파일을 나중에 `.gitignore`에 넣었는데 계속 올라감 | `git rm --cached 파일명` 후 커밋. 깃 추적에서만 빠지고 내 컴퓨터 파일은 남음 |
| PR base를 main으로 올림 | PR 제목 옆 **Edit** → base를 `dev`로 |
| 커밋 메시지 오타 (푸시 전) | `git commit --amend -m "새 메시지"` (어멘드, 직전 커밋 고치기) |
| 커밋 메시지 오타 (푸시 후) | 그냥 둠. 강제로 고치려면 포스 푸시가 필요한데 금지임 |
| 지금 어느 브랜치인지 모르겠음 | `git branch` → `*` 붙은 게 현재 브랜치 |
| 기능 동결 후 새 아이디어 | 깃허브 Issue(할 일 게시판)에 적어두고 구현하지 않음 |

**[A] dev에 커밋해버렸을 때 (푸시 전)**

내 커밋을 내 브랜치로 옮기고, dev는 깃허브 상태로 되돌림. 리셋(reset, 브랜치를 특정 시점으로 되돌리기)의 `--hard`는 변경까지 버리므로 **반드시 앞의 두 줄을 먼저 실행**함.

```bash
# ===== 실행할 명령어 =====
git switch yoon_habeen
git merge dev                             # dev에 잘못 한 커밋을 내 브랜치로 가져옴
git switch dev
git reset --hard origin/dev               # dev를 깃허브 상태로 되돌림
git switch yoon_habeen
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
| base / compare | PR의 목적지 브랜치(dev) / 내 이름 브랜치 |
| 초안 PR(Draft PR) | 작업 중임을 표시한 PR. 머지가 막힘 |
| 리뷰(review) | 팀원이 변경 코드를 확인하고 의견을 남기는 것 |
| 어프루브(Approve) | "머지해도 됨" 공식 승인 = 따봉 |
| Request changes | 수정 요청. 고칠 때까지 머지 막힘 |
| Resolve conversation | 리뷰 코멘트를 해결 처리 |
| 머지 커밋(Merge commit) | 내 커밋을 그대로 보존하며 합치기. 우리는 모든 머지를 이 방식으로 함 |
| 리버트(revert) | 특정 커밋을 거꾸로 되돌리는 새 커밋 만들기 |
| 리셋(reset) | 브랜치를 특정 시점으로 되돌리기 (`--soft` 파일 유지, `--hard` 변경까지 버림) |
| 스태시(stash) | 커밋 안 한 변경을 임시 보관함에 넣기 |
| 어멘드(amend) | 직전 커밋 고치기 (푸시 전만) |
| 포스 푸시(force push) | 깃허브 이력을 강제로 덮어쓰기. 금지 |
| 태그(tag) | 특정 커밋에 붙이는 버전 이름표 (`v1.0.0`) |
| 마일스톤(milestone) | 중간 목표 시점. 우리는 M1~M4 |
| 핫픽스(hotfix) | 배포본(main) 긴급 수정 |
| 브랜치 보호 규칙(Ruleset) | 직접 푸시 금지·승인 2개 같은 브랜치 제한 설정 |
| .gitignore | 깃이 무시할 파일 목록 |
| .gitattributes | 파일 종류별 깃 처리 방식(줄바꿈 등)을 정하는 파일 |
| 마이그레이션(migration) | DB 테이블 변경 SQL을 순서대로 쌓는 파일 |
| 이슈(Issue) | 깃허브의 할 일·버그 게시판 |
| Organization | 팀 단위로 레포와 멤버를 관리하는 깃허브 계정 |
