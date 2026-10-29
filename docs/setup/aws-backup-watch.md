# AWS S3 백업 감시 설치 기록 (EventBridge 예약 → Lambda → SNS)

- 담당: 황채연 (AWS)
- 관련 WBS: 없음 (AWS 추가 작업 · S3 백업 감시)
- 버전: versions.md 참고 (Lambda Python 3.12 · AWS CLI v2)
- 상태: **2026-10-28 07:00 첫 자동 점검 정상** (`last backup 2026-10-28 03:41 KST, age 3.3h, limit 26h`) · 경보 시험은 메일 · Slack 둘 다 수신 확인
- 관련: [`aws-s3-backup.md`](aws-s3-backup.md) (repo2 = S3 백업), [`aws-security-alerts.md`](aws-security-alerts.md) (경보가 모이는 SNS · Slack), [`aws-dr-mentor.md`](aws-dr-mentor.md) (복원 훈련)

## 0. 결과 요약

| 시험 | 결과 |
|---|---|
| 기준을 0.1시간으로 낮춰 실행 (10/27) | `ALARM` → 메일 수신 · Slack 은 처음에 안 옴 → 메시지 형식 고친 뒤 수신 (5장) |
| 기준 26시간으로 실행 | `OK` |
| 매일 07:00 자동 점검 첫 실행 (10/28 07:00:41) | `last backup 2026-10-28 03:41 KST, age 3.3h, limit 26h` → `OK` |
| 경보 메일 본문 (10/28) | 한국어 문장 2줄 (JSON 아님) |

## 1. 왜 / 무엇을

- 10/27 에 03:40 · 11:18 백업이 실패했는데, 알림이 없어 DR 복원이 2배 느려진 것(320초)을 보고서야 알았다 → [`aws-rds-compare.md`](aws-rds-compare.md)
- 백업은 사내(db-01)가 S3 로 올리지만, **"백업이 안 들어왔다"는 사실은 AWS 쪽에서 봐야** 사내가 통째로 멈춰도 알 수 있다
- 그래서 AWS 가 매일 07:00 에 S3 를 직접 보고, 마지막 백업이 26시간보다 오래됐으면 경보를 보낸다

```text
EventBridge 예약 (매일 07:00 KST)
   └─▶ Lambda chargeops-backup-watch
          ├─ S3 backup.info 의 마지막 수정 시각 확인 (= 마지막 성공 백업)
          └─ 26시간 넘음 → SNS chargeops-alerts ─┬─▶ 메일 (평문)
                                                └─▶ Amazon Q Developer → Slack #chargeops-alerts
```

| 구성 | 이름 · 값 |
|---|---|
| 확인하는 파일 | 백업 버킷의 `chargeops-mentor/backup/chargeops/backup.info` — pgBackRest 가 백업에 성공할 때마다 새로 씀 |
| Lambda | `chargeops-backup-watch` · Python 3.12 · 제한 시간 15초 · 환경 변수 `BUCKET` · `KEY` · `TOPIC_ARN` · `MAX_AGE_HOURS=26` |
| IAM 역할 | `chargeops-backup-watch-role` — 로그 쓰기(`AWSLambdaBasicExecutionRole`) + 인라인 `chargeops-backup-watch-read` |
| 예약 | EventBridge `chargeops-backup-watch-daily` · `cron(0 22 * * ? *)` (UTC 22:00 = KST 07:00) |
| 알림 | SNS `chargeops-alerts` (alerts 스택) → 메일 · Slack `#chargeops-alerts` |

**왜 26시간** — 백업은 매일 03:40 에 한 번 돈다. 07:00 점검 때 정상이면 마지막 백업은 약 3시간 전이다. 오늘 03:40 백업이 실패하면 마지막 성공은 어제 03:40 → 07:00 기준 약 27시간 → 경보. 24시간으로 잡으면 백업이 조금만 늦어져도 울린다.

**최소 권한** — 키 없이 역할로만 동작하고, 할 수 있는 일은 둘뿐이다.

| 권한 | 범위 |
|---|---|
| `s3:GetObject` (HEAD 포함) | `backup.info` 파일 하나 |
| `sns:Publish` | `chargeops-alerts` 토픽 하나 |

## 2. 설치 명령어

**▶ AWS 콘솔 CloudShell (서울)** — 계정 번호 · 토픽 주소는 명령이 채우고, 백업 버킷 이름만 직접 입력한다.

### 2-1. 변수 · IAM 역할

```bash
# ===== 실행할 명령어 =====
ACC=$(aws sts get-caller-identity --query Account --output text)
T=$(aws sns list-topics --query "Topics[?ends_with(TopicArn, ':chargeops-alerts')].TopicArn" --output text)
KEY=chargeops-mentor/backup/chargeops/backup.info
read -rp "backup bucket: " B                                # <각자 설정> 백업 버킷 이름
aws s3api head-object --bucket $B --key $KEY --query LastModified --output text   # 마지막 백업 시각 (UTC) 이 나와야 함

cat > /tmp/trust.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
aws iam create-role --role-name chargeops-backup-watch-role --assume-role-policy-document file:///tmp/trust.json --query Role.RoleName --output text
aws iam attach-role-policy --role-name chargeops-backup-watch-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
cat > /tmp/watch-policy.json <<EOF
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":"s3:GetObject","Resource":"arn:aws:s3:::$B/$KEY"},
  {"Effect":"Allow","Action":"sns:Publish","Resource":"$T"}
]}
EOF
aws iam put-role-policy --role-name chargeops-backup-watch-role --policy-name chargeops-backup-watch-read --policy-document file:///tmp/watch-policy.json
```

### 2-2. Lambda

코드는 3장의 `backup_watch.py` 를 CloudShell 의 `~/backup_watch.py` 로 저장한다.

```bash
# ===== 실행할 명령어 =====
cd ~ && zip -j /tmp/backup_watch.zip backup_watch.py
# 역할을 방금 만들었으면 10초쯤 기다린 뒤 (역할이 퍼지는 시간)
aws lambda create-function --function-name chargeops-backup-watch --runtime python3.12 --handler backup_watch.handler --timeout 15 --role arn:aws:iam::$ACC:role/chargeops-backup-watch-role --zip-file fileb:///tmp/backup_watch.zip --environment "Variables={BUCKET=$B,KEY=$KEY,TOPIC_ARN=$T,MAX_AGE_HOURS=26}" --query State --output text
# 코드를 고친 뒤에는
# aws lambda update-function-code --function-name chargeops-backup-watch --zip-file fileb:///tmp/backup_watch.zip --query LastUpdateStatus --output text
```

### 2-3. 매일 07:00 예약

```bash
# ===== 실행할 명령어 =====
aws events put-rule --name chargeops-backup-watch-daily --schedule-expression "cron(0 22 * * ? *)" --state ENABLED --query RuleArn --output text
aws lambda add-permission --function-name chargeops-backup-watch --statement-id events-daily --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn arn:aws:events:ap-northeast-2:$ACC:rule/chargeops-backup-watch-daily --query Statement --output text >/dev/null
aws events put-targets --rule chargeops-backup-watch-daily --targets "Id=backup-watch,Arn=arn:aws:lambda:ap-northeast-2:$ACC:function:chargeops-backup-watch"
```

### 2-4. 되돌리기 (필요할 때만)

```bash
# ===== 실행할 명령어 =====
aws events remove-targets --rule chargeops-backup-watch-daily --ids backup-watch
aws events delete-rule --name chargeops-backup-watch-daily
aws lambda delete-function --function-name chargeops-backup-watch
aws iam delete-role-policy --role-name chargeops-backup-watch-role --policy-name chargeops-backup-watch-read
aws iam detach-role-policy --role-name chargeops-backup-watch-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name chargeops-backup-watch-role
```

## 3. 설정 파일

| 파일 · 설정 | 위치 | git |
|---|---|---|
| `backup_watch.py` (아래) | Lambda `chargeops-backup-watch` | ✅ (이 문서) |
| 환경 변수 `BUCKET` · `TOPIC_ARN` | Lambda 설정 | ✗ (버킷 이름 · 계정 번호) |

```python
import os, json, datetime, boto3

s3 = boto3.client("s3")
sns = boto3.client("sns")

def alert(subject, message):
    # Amazon Q Developer(Slack) 는 정해진 형식(custom notification)만 받는다 → 메일은 평문, https 는 JSON
    chat = {"version": "1.0", "source": "custom",
            "content": {"textType": "client-markdown", "title": subject, "description": message}}
    body = {"default": message, "email": message, "https": json.dumps(chat, ensure_ascii=False)}
    sns.publish(TopicArn=os.environ["TOPIC_ARN"], Subject=subject,
                MessageStructure="json", Message=json.dumps(body, ensure_ascii=False))

def handler(event, context):
    bucket, key = os.environ["BUCKET"], os.environ["KEY"]
    limit = float((event or {}).get("max_age_hours", os.environ.get("MAX_AGE_HOURS", "26")))
    try:
        last = s3.head_object(Bucket=bucket, Key=key)["LastModified"]
    except Exception as e:
        alert("[ChargeOps] S3 백업 확인 실패", f"backup.info를 읽지 못했어요: {e}")
        raise
    age = (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds() / 3600
    kst = (last + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    print(f"last backup {kst} KST, age {age:.1f}h, limit {limit:g}h")
    if age > limit:
        alert("[ChargeOps] S3 백업 지연",
              f"repo2(S3) 마지막 백업: {kst} (한국 시간), {age:.1f}시간 전 (기준 {limit:g}시간).\n"
              "db-01의 chargeops-backup.service 상태와 /var/log/pgbackrest/chargeops-backup.log를 확인하세요.")
        return {"status": "ALARM", "age_hours": round(age, 1)}
    return {"status": "OK", "age_hours": round(age, 1)}
```

- 시험할 때는 이벤트로 기준을 낮춘다: `{"max_age_hours": 0.1}` (환경 변수는 그대로)

## 4. 확인 방법

```bash
# ===== 실행할 명령어 =====
# 경보 시험: 기준 0.1시간 → ALARM · 메일 · Slack 이 와야 함
aws lambda invoke --function-name chargeops-backup-watch --cli-binary-format raw-in-base64-out --payload '{"max_age_hours":0.1}' /tmp/out.json >/dev/null; cat /tmp/out.json; echo
# 평소 기준 (26시간) → OK
aws lambda invoke --function-name chargeops-backup-watch /tmp/out.json >/dev/null; cat /tmp/out.json; echo
# 자동 점검 기록
aws logs tail /aws/lambda/chargeops-backup-watch --since 24h | grep "last backup"
aws events list-rules --query "Rules[?Name=='chargeops-backup-watch-daily'].[Name,State,ScheduleExpression]" --output table
```
```
# ===== 실행 결과 요약 (2026-10-27 ~ 28) =====
시험 (0.1시간)   {"status": "ALARM", ...} → 메일 "[ChargeOps] S3 백업 지연" · Slack "Custom notification delivered by Amazon Q Developer"
평소 (26시간)    {"status": "OK", ...}
자동 (10/28)     07:00:41  last backup 2026-10-28 03:41 KST, age 3.3h, limit 26h
예약 규칙        chargeops-backup-watch-daily | ENABLED | cron(0 22 * * ? *)
```

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| 경보 메일은 오는데 Slack 에 안 옴 | Amazon Q Developer 는 정해진 형식(`{"version":"1.0","source":"custom","content":{…}}`)이 아닌 SNS 메시지를 버린다 → `MessageStructure=json` 으로 메일은 평문, https(Slack) 는 custom 형식으로 나눠 보냄 (10/28) |
| Slack 채널을 `#ops-alerts` 로 잘못 알고 있었음 | 실제 연결은 `#chargeops-alerts` (Amazon Q 설정 `chargeops-ops-alerts-slack`). `infra/terraform/alerts/main.tf` 주석도 고침 |
| (주의) 멘토 서버 VM 이 꺼져 있으면 03:40 백업이 안 돌아 다음 날 07:00 경보 | 의도대로. 끄기 전에 팀에 알린다 |

## 6. 비용 (서울, 근사치 — 청구서로 확인)

| 무엇 | 언제 | 대략 |
|---|---|---|
| Lambda 하루 1번 · 1초 미만 | 상시 | 무료 범위 |
| EventBridge 예약 · S3 HEAD 요청 · 로그 몇 줄 | 상시 | 1센트 미만 |

## 7. 다음

- (선택) 같은 Lambda 로 WAL 보관 지연도 본다 — 백업은 하루 1번이지만 WAL 은 1분마다 올라간다
- 프로젝트 끝: 2-4 되돌리기 명령으로 삭제
