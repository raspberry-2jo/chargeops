# AWS Grafana 대시보드 반영 절차 (온프렘 JSON → AWS)

- 담당: 황채연 (AWS)
- 관련 WBS: 3.22 AWS 대시보드 반영 (자동화 대신 수동 절차)
- 버전: versions.md 참고
- 상태: 2026-10-26 `ChargeOps 관제 (통합)` 반영에 사용 (version 1 → 2, 패널 25개)
- 앞 단계: AWS Grafana DB 연결(VPN · 데이터 소스 `chargeops-db`)은 팀 내부 실행표 v2 참고 (공개 저장소에 올리지 않음)

## 1. 왜 / 무엇을

- AWS Grafana(EC2 `chargeops-metrics`)는 **온프렘이 멈춰도 보는 관제 화면**이다. 대시보드 JSON은 자동으로 맞추지 않고 **이 절차로 사람이 반영**한다.
- 언제: `monitoring/dashboard-*.json` 을 바꾼 PR이 생겼을 때. 온프렘에 반영한 뒤 **머지 전에** 한다. PR 점검 항목을 체크해야 하기 때문이다. 머지 뒤라면 `BR=dev` 로 한다.
- 누가: AWS 담당. 다른 사람이 올린 PR이면 슬랙으로 반영을 요청한다.
- 걸리는 시간: 약 10분
- 전제: AWS의 데이터 소스 uid가 온프렘과 같다. 그래서 JSON을 고치지 않고 그대로 올린다.

| 데이터 소스 | uid | 종류 |
|---|---|---|
| Prometheus (온프렘 remote_write 사본) | `prometheus` | prometheus |
| ChargeOps DB (AWS) | `chargeops-db` | grafana-postgresql-datasource |

```text
GitHub  <BR>/monitoring/dashboard-*.json
   │ 2-1 받기
   ▼
AWS Grafana EC2 ─ 2-2 백업 ─ 2-3 점검A 데이터 소스 ─ 2-4 점검B grafana_aws 권한 ─ 2-5 올리기 · 확인
                                                       (PR 점검 항목 2)              (PR 점검 항목 1)
```

| PR 점검 항목 | 이 문서에서 |
|---|---|
| AWS Grafana에도 같은 JSON을 반영함 (반영자 · 반영 시각) | 2-5. 반영 시각은 2-5가 출력하는 `synced at` |
| 새로 읽는 DB 테이블·뷰가 있으면 grafana_aws에 SELECT 권한을 추가함 | 2-4. `NO` 가 나오면 DB 팀에 요청하고, 권한이 생기면 체크 |

## 2. 실행 명령어 (EC2에서)

접속: AWS 콘솔 → EC2 → `chargeops-metrics` 선택 → 연결 → Session Manager → 연결

- ⚠ **mgmt가 아니라 EC2에서 친다.** mgmt에는 `localhost:3000` 이 없어서, 쳐도 아무것도 바뀌지 않는다.
- Session Manager 터미널은 여러 줄 붙여넣기(heredoc)가 깨진다. 그래서 아래 명령은 **전부 한 줄**이다.
- 20분 동안 입력이 없으면 세션이 끊기고 변수(`GPW` · `BR` · `T` · `U`)가 사라진다. 파일은 남는다. 끊기면 2-0부터 다시 한다.

### 2-0. 관리자 비밀번호 입력 · 로그인 확인

```bash
# ===== 실행할 명령어 =====
# 빈 값이면 다시 묻는다. "Grafana admin password:" 가 보일 때 한 번만 입력 (화면에 안 보이는 게 정상)
GPW=; until [ -n "$GPW" ]; do read -rsp "Grafana admin password: " GPW; echo; done; echo "len=${#GPW} nonascii=$(printf %s "$GPW" | LC_ALL=C tr -d '\000-\177' | wc -c)"; curl -s -o /dev/null -w "login=%{http_code}\n" -u "admin:$GPW" http://localhost:3000/api/user
```

- `len=<숫자> nonascii=0` 과 `login=200` 이 나오면 다음으로 간다.
- `login=401` 이면 비밀번호가 틀린 것이다. 다시 친다.
- `nonascii` 가 0이 아니면 한글 입력 상태였다. 영문으로 바꿔서 다시 친다.
- `len=` 이 나온 뒤에는 **비밀번호를 다시 치지 않는다.** 다시 치면 명령어로 실행돼 셸 기록에 남는다.

### 2-1. 새 JSON 받기

```bash
# ===== 실행할 명령어 =====
# BR: 머지 전이면 PR 브랜치 이름(예: kang_youngsoo), 머지 뒤면 dev · F: 바꾼 대시보드 파일
BR=dev; F=monitoring/dashboard-ops.json; T=$(date +%m%d-%H%M); curl -sfL "https://raw.githubusercontent.com/raspberry-2jo/chargeops/$BR/$F" -o ~/dash-new-$T.json && U=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['uid'])" ~/dash-new-$T.json) && python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print('uid',d['uid'],'| title',d['title'],'| panels',len(d['panels']))" ~/dash-new-$T.json || echo "DOWNLOAD FAILED - check BR / F"
```

- `uid … | title … | panels …` 가 나오면 다음으로 간다.

### 2-2. 지금 AWS 대시보드 백업

```bash
# ===== 실행할 명령어 =====
curl -sf -u "admin:$GPW" "http://localhost:3000/api/dashboards/uid/$U" -o ~/dash-backup-$T.json && python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print('backup',d['dashboard']['title'],'| version',d['dashboard'].get('version'),'| folder',d['meta'].get('folderUid') or 'General')" ~/dash-backup-$T.json || echo "BACKUP FAILED - stop"
```

- `backup … | version N | folder …` 가 나오면 다음으로 간다.
- `BACKUP FAILED` 가 나오면 두 경우 중 하나다.
  - 비밀번호 문제: 2-0을 다시 한다.
  - AWS에 아직 없는 새 대시보드: 이 절차 대신 웹에서 처음 올린다 (Dashboards → New → Import).

### 2-3. 점검 A: 데이터 소스

```bash
# ===== 실행할 명령어 =====
curl -s -u "admin:$GPW" http://localhost:3000/api/datasources -o ~/ds-$T.json && python3 -c "import json,sys;w=lambda o:([o['datasource']['uid']] if isinstance(o,dict) and isinstance(o.get('datasource'),dict) and o['datasource'].get('uid') else [])+sum((w(v) for v in (o.values() if isinstance(o,dict) else o if isinstance(o,list) else [])),[]);have={x['uid'] for x in json.load(open(sys.argv[1]))};need=set(w(json.load(open(sys.argv[2]))));print('need',sorted(need));print('missing',sorted(x for x in need-have if not x.startswith('--')))" ~/ds-$T.json ~/dash-new-$T.json
```

- `missing []` 이 나오면 다음으로 간다.
- `--` 로 시작하는 uid(`-- Mixed --`, `-- Grafana --`)는 Grafana에 기본으로 들어 있는 것이라 자동으로 뺀다.
- `missing` 에 이름이 있으면 **멈춘다.** AWS에 그 uid로 데이터 소스를 먼저 만들어야 한다. DB 쪽 데이터 소스라면 DB 팀과 상의한다.
- `${DS_...}` 가 보이면 JSON을 "외부 공유용(Export for sharing externally)"으로 내보낸 것이다. 작성자에게 그 옵션을 끄고 다시 내보내 달라고 요청한다.

### 2-4. 점검 B: grafana_aws 읽기 권한 (PR 점검 항목 2)

JSON의 SQL에서 FROM · JOIN 뒤에 오는 이름을 뽑는다. 그다음 **grafana_aws 계정으로 직접** DB에 물어서 읽기(SELECT) 권한이 있는지 확인한다. 읽기만 하고, DB 설정은 바꾸지 않는다.

```bash
# ===== 실행할 명령어 =====
# ① 이름을 뽑아 확인용 SQL 파일 만들기
python3 -c "import json,re,sys;w=lambda o:([o['rawSql']] if isinstance(o,dict) and isinstance(o.get('rawSql'),str) else [])+sum((w(v) for v in (o.values() if isinstance(o,dict) else o if isinstance(o,list) else [])),[]);t=sorted({m.lower() for s in w(json.load(open(sys.argv[1]))) for m in re.findall(r'(?:from|join)\s+([A-Za-z_][\w.]*)',s,re.I)});q=chr(39);a=lambda x:q+x+q;open('/tmp/tbl-check.sql','w').write('select s, t from (select t, case when to_regclass(t) is null then '+a('-')+' when has_table_privilege(t,'+a('SELECT')+') then '+a('OK')+' else '+a('NO')+' end s from unnest(array['+','.join(a(x) for x in t)+']::text[]) t) x order by array_position(array['+a('NO')+','+a('OK')+','+a('-')+'], s), t;\n');print('names',len(t))" ~/dash-new-$T.json
# ② grafana_aws로 확인 (비밀번호 · CA는 Grafana가 쓰는 파일을 그대로 씀. psql이 없으면 sudo dnf install -y postgresql15)
sudo bash -c 'PGPASSWORD="$(cat /etc/grafana/secrets/grafana_aws.pw)" psql "host=10.10.30.12 port=5432 dbname=chargeops user=grafana_aws sslmode=verify-full sslrootcert=/etc/grafana/secrets/chargeops-ca.crt" -At -F " " -f /tmp/tbl-check.sql'; rm -f /tmp/tbl-check.sql
```

| 결과 줄 | 뜻 | 할 일 |
|---|---|---|
| `NO <이름>` | 표·뷰는 있는데 grafana_aws가 못 읽는다. 그 패널은 AWS에서 `permission denied` 가 난다 | DB 팀에 요청한다 (아래 문구). **직접 GRANT 하지 않는다** |
| `OK <이름>` | 읽기 권한이 있다 | 없음 |
| `- <이름>` | 표·뷰가 아니다 (`generate_series` 같은 함수, `now`, `lateral`, WITH 별칭 등) | 무시 |

DB 팀 요청 문구:

> grafana_aws에 SELECT 추가 부탁드려요: public.<이름>, public.<이름> (대시보드 `<파일>` · PR #<번호>)

`NO` 가 있어도 2-5(올리기)는 해도 된다. 권한이 생길 때까지 그 패널만 에러가 난다. PR 점검 항목 2는 권한 추가가 끝난 뒤에 체크한다.

### 2-5. 올리기 (변경) · 확인

```bash
# ===== 실행할 명령어 =====
# ① 올릴 파일 만들기 (id · version · 폴더는 지금 AWS 것을 이어받음)
python3 -c "import json,sys;o=json.load(open(sys.argv[1]));n=json.load(open(sys.argv[2]));n['id']=o['dashboard']['id'];n['version']=o['dashboard'].get('version');json.dump({'dashboard':n,'folderUid':o['meta'].get('folderUid',''),'overwrite':True,'message':'sync from '+sys.argv[3]},open(sys.argv[4],'w'),ensure_ascii=False);print('ready uid',n['uid'],'| panels',len(n['panels']))" ~/dash-backup-$T.json ~/dash-new-$T.json "$BR" ~/dash-upload-$T.json
# ② 올리기 + 반영 시각 출력 (PR 점검 항목 1의 "반영 시각"에 적음)
curl -s -u "admin:$GPW" -H "Content-Type: application/json" -X POST http://localhost:3000/api/dashboards/db -d @$HOME/dash-upload-$T.json; echo; date "+synced at %F %H:%M %Z"
# ③ 반영 확인: version이 백업보다 1 올라가야 함
curl -s -u "admin:$GPW" "http://localhost:3000/api/dashboards/uid/$U" | python3 -c "import json,sys;d=json.load(sys.stdin)['dashboard'];print('version',d.get('version'),'| panels',len(d['panels']))"
```

- ② 결과에 `"status":"success"` 와 `synced at …` 이 나와야 한다.
- ③ 결과의 version이 2-2 백업보다 1 커야 한다. panels 수는 2-1과 같아야 한다.

### 2-6. 정리

```bash
# ===== 실행할 명령어 =====
unset GPW
```

백업 파일(`~/dash-backup-*.json`)은 지우지 않는다. 되돌릴 때 쓴다.

### 2-7. 되돌리기 (필요할 때만)

```bash
# ===== 실행할 명령어 =====
# 2-0을 먼저 한다. T가 사라졌으면 ls -t ~/dash-backup-*.json | head -3 으로 파일을 찾고 T=<날짜-시각> 으로 다시 넣는다
python3 -c "import json,sys;b=json.load(open(sys.argv[1]));json.dump({'dashboard':b['dashboard'],'folderUid':b['meta'].get('folderUid',''),'overwrite':True,'message':'rollback'},open(sys.argv[2],'w'),ensure_ascii=False);print('rollback ready')" ~/dash-backup-$T.json ~/dash-rollback-$T.json && curl -s -u "admin:$GPW" -H "Content-Type: application/json" -X POST http://localhost:3000/api/dashboards/db -d @$HOME/dash-rollback-$T.json; echo
```

## 3. 설정 파일

- 바꾸는 설정 파일은 없다. 대시보드는 Grafana 내부 DB에 저장된다.
- EC2 홈 폴더에 남는 파일은 다음과 같다.

| 파일 | 내용 |
|---|---|
| `dash-new-<T>.json` | GitHub에서 받은 JSON |
| `dash-backup-<T>.json` | 반영 전 AWS 대시보드 (되돌리기용) |
| `ds-<T>.json` | AWS 데이터 소스 목록 |
| `dash-upload-<T>.json` | 실제로 올린 것 |

## 4. 확인 방법

- 브라우저에서 AWS Grafana → 해당 대시보드를 열고 새로고침한다.
  - 바뀐 패널이 보이는지 확인한다.
  - 패널 왼쪽 위에 빨간 ⚠ 가 없는지 확인한다.
- ⚠ 에 마우스를 올렸을 때 `permission denied for table …` 이 보이면, 2-4의 `NO` 와 같은 이름일 것이다. DB 팀에 요청한다.
- 반영 기록은 대시보드 → 톱니바퀴(Settings) → Versions에 남는다. 메시지 `sync from <BR>`, 되돌린 경우 `rollback` 이다.

```
# ===== 실행 결과 (2026-10-26, ChargeOps 관제 (통합)) =====
version 1 → 2 · panels 25 · 데이터 소스 누락 없음 (기본 내장 -- Mixed -- 만 추가로 보임)
```

## 5. 막혔던 점 (2026-10-26 첫 반영)

| 증상 | 원인 | 해결 |
|---|---|---|
| 명령이 아무것도 출력하지 않고 끝났고, 다음 단계에서 `FileNotFoundError` | mgmt에서 실행했다. mgmt의 `localhost:3000` 에는 Grafana가 없고, `curl -s` 가 에러를 숨겼다 | EC2에서 실행한다. 지금 명령은 `-f` 옵션과 `FAILED` 출력으로 바로 알 수 있다 |
| `len=0` 이 나온 뒤, 다음 줄에 친 비밀번호가 `command not found` 로 화면과 셸 기록에 남음 | 명령을 붙여넣을 때 Enter가 같이 들어가서 빈 값이 입력됐다 | 2-0의 "빈 값이면 다시 묻기"를 쓴다. 노출되면 바로 Grafana 비밀번호를 바꾼다 |
| 여러 줄 Python(heredoc)이 붙여넣기 중에 깨짐 | Session Manager 웹 터미널의 한계 | 전부 한 줄 명령으로 바꿨다 |
| `KeyError: 'dashboard'` | 응답이 대시보드가 아니라 401 오류였다 (`GPW` 가 비어 있었음) | 2-0을 다시 한다 |
| 데이터 소스 비교에 `-- Mixed --` 가 나옴 | 한 패널에서 여러 데이터 소스를 섞어 쓸 때 붙는 Grafana 기본 내장 이름 | 무시한다. 2-3이 자동으로 뺀다 |
