# AWS Grafana EC2 구축 기록 (온프렘 지표 사본 · 장애 때 보는 관제 화면)

- 담당: 황채연 (AWS)
- 관련 WBS: 3.22 AWS 대시보드 (B안: 온프렘 Prometheus → AWS)
- 버전: versions.md 참고 (Amazon Linux 2023 · Grafana 13.2.4 · nginx 1.30.5 · VictoriaMetrics)
- 상태: **2026-10-22 구축 · 대시보드 6개 이관**, 10/26 관제(통합) 반영 · DB 패널 연결, 10/27 CloudWatch 데이터 소스 · 2026-10-28 설정 확인
- 관련: [`aws-grafana-dashboard-sync.md`](aws-grafana-dashboard-sync.md) (대시보드 반영 절차), [`aws-security-alerts.md`](aws-security-alerts.md)
- DB 패널용 VPN 연결 세부(주소 · 키 · 계정)는 팀 내부 실행표 v2 참고 (공개 저장소에 올리지 않음)

## 0. 결과 요약

| 항목 | 결과 |
|---|---|
| 서버 | EC2 `chargeops-metrics` · t3.small · Amazon Linux 2023 · 별도 VPC |
| 지표 경로 | 온프렘 Prometheus → `remote_write` → HTTPS 443 (nginx, basic auth) → VictoriaMetrics (14일 보관) → Grafana |
| 들어오는 길 | 443 (remote_write) · 3000 (Grafana 화면) — 각각 허용 IP 2개만. **SSH 22 없음** |
| 접속 | Session Manager (인스턴스 역할 `chargeops-metrics-role`) |
| 데이터 소스 | `prometheus` (VictoriaMetrics, 온프렘과 같은 uid) · `chargeops-db` (DB, VPN 경유 읽기 전용) · CloudWatch |
| 대시보드 | 10/22 6개 이관 (서버 8대 · DMZ, 관제 지표, kube-prometheus-stack 기본 4종), 10/26 `ChargeOps 관제 (통합)` 패널 25개 |
| 끊김 시험 (10/22) | nginx 정지 → 재시작: 재시작 직후 밀린 지표가 들어와 **빈 구간이 채워짐** |

## 1. 왜 / 무엇을

- 온프렘 관제 센터가 멈추면 온프렘 Grafana 도 같이 멈춘다. **장애가 났을 때 볼 화면을 AWS 에 따로 둔다**
- 온프렘이 지표를 AWS 로 **밀어 보낸다**(remote_write). AWS 에서 온프렘으로 들어가는 길은 만들지 않는다 → 온프렘은 들어오는 포트 0 그대로
- 지표 저장은 Prometheus 대신 가벼운 VictoriaMetrics(Prometheus 호환)를 쓰고 14일만 보관한다 (디스크 · 비용)

```text
온프렘 Prometheus ──remote_write (HTTPS 443, basic auth)──▶ nginx :443  /api/v1/write
                                                              └─▶ VictoriaMetrics 127.0.0.1:8428 (14일)
운영자 브라우저 ──(허용 IP)──▶ Grafana :3000 ─┬─ prometheus 데이터 소스 → VictoriaMetrics
                                             ├─ chargeops-db → VPN → db-02 (읽기 전용)
                                             └─ CloudWatch (인스턴스 역할)
관리 ── Session Manager (SSH 없음)
```

## 2. 구성

### 2-1. 서버 · 보안 그룹 · 역할

| 구성 | 값 |
|---|---|
| EC2 | `chargeops-metrics` · t3.small · Amazon Linux 2023 · 고정 공인 IP (주소는 `<각자 설정>`) |
| 보안 그룹 `chargeops-metrics-sg` 들어오는 규칙 | tcp 443 (허용 IP 2개) · tcp 3000 (허용 IP 2개) · **22 없음** |
| 인스턴스 역할 `chargeops-metrics-role` | `AmazonSSMManagedInstanceCore` + 인라인 `chargeops-grafana-cloudwatch-read` · `chargeops-grafana-loggroups-list` |

CloudWatch 데이터 소스용 권한 (읽기만, CloudShell). 다시 만들 때의 명령이고, 지금 붙어 있는 내용은 `aws iam get-role-policy --role-name chargeops-metrics-role --policy-name chargeops-grafana-cloudwatch-read` 로 확인한다:

```bash
# ===== 실행할 명령어 =====
aws iam put-role-policy --role-name chargeops-metrics-role --policy-name chargeops-grafana-cloudwatch-read --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["cloudwatch:DescribeAlarmsForMetric","cloudwatch:DescribeAlarmHistory","cloudwatch:DescribeAlarms","cloudwatch:ListMetrics","cloudwatch:GetMetricStatistics","cloudwatch:GetMetricData","ec2:DescribeTags","ec2:DescribeInstances","ec2:DescribeRegions","tag:GetResources"],"Resource":"*"}]}'
aws iam put-role-policy --role-name chargeops-metrics-role --policy-name chargeops-grafana-loggroups-list --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"logs:DescribeLogGroups","Resource":"*"}]}'
```

### 2-2. EC2 안의 서비스

| 서비스 | 설정 (2026-10-28 확인) |
|---|---|
| nginx | `listen 443 ssl` · `location = /api/v1/write` → `auth_basic "remote_write"` (`/etc/nginx/.htpasswd`) → `proxy_pass http://127.0.0.1:8428` |
| VictoriaMetrics | `victoria-metrics-prod -storageDataPath=/victoria-metrics-data -retentionPeriod=14d` (8428, 로컬만) |
| Grafana | `grafana-server` 13.2.4 · 3000 |
| SSM 에이전트 | `amazon-ssm-agent` |

- 비밀번호 파일(`.htpasswd`) · TLS 인증서 · Grafana 관리자 비밀번호 = `<각자 설정>`. 화면 · 채팅 · Git 에 남기지 않는다 (Grafana 관리자 비밀번호는 `read -rsp` 로만 입력)

### 2-3. 온프렘 Prometheus

```yaml
# ===== 설정 (값은 각자 설정) =====
remote_write:
  - url: https://<Grafana EC2 주소>/api/v1/write
    basic_auth:
      username: <각자 설정>
      password: <각자 설정>
```

- TLS 인증서 검증 방식 · 설정 위치는 팀 내부 실행표 참고

### 2-4. Grafana 데이터 소스 · 대시보드

| 데이터 소스 | uid | 연결 |
|---|---|---|
| Prometheus (온프렘 remote_write 사본) | `prometheus` | VictoriaMetrics (EC2 안) |
| ChargeOps DB (AWS) | `chargeops-db` | VPN → db-02 · 계정 `grafana_aws` (읽기 전용, 권한은 DB 팀 마이그레이션) |
| CloudWatch | — | 인증 AWS SDK Default (인스턴스 역할) · 리전 ap-northeast-2 |

- 데이터 소스 uid 를 온프렘과 같게 맞춰서 대시보드 JSON 을 고치지 않고 그대로 올린다 → [`aws-grafana-dashboard-sync.md`](aws-grafana-dashboard-sync.md)

## 3. 설정 파일

| 파일 | 위치 | git |
|---|---|---|
| `/etc/nginx/conf.d/*.conf` | EC2 | ✗ (2-2 에 요약) |
| `/etc/nginx/.htpasswd` · TLS 인증서 · 키 | EC2 | ✗ (비밀값) |
| Prometheus `remote_write` 주소 · 계정 | 온프렘 | ✗ (`<각자 설정>`) |
| 대시보드 JSON | `monitoring/dashboard-*.json` | ✅ (온프렘과 같은 파일) |

## 4. 확인 방법

**▶ 콘솔 → EC2 → `chargeops-metrics` → 연결 → Session Manager** (한 줄씩)

```bash
# ===== 실행할 명령어 =====
head -n 2 /etc/os-release; grafana-server -v 2>/dev/null | head -n 1; nginx -v 2>&1
systemctl list-units --type=service --state=running --no-pager | grep -i -E "grafana|nginx|ssm"
ps -eo args | grep -i victoria | grep -v grep
sudo grep -h -E "listen|server_name|location|proxy_pass|auth_basic" /etc/nginx/conf.d/*.conf
```

**▶ CloudShell** — 열린 포트 (IP 대신 개수만)

```bash
# ===== 실행할 명령어 =====
aws ec2 describe-security-groups --filters "Name=group-name,Values=chargeops-metrics-sg" --query "SecurityGroups[].IpPermissions[].[IpProtocol,FromPort,ToPort,length(IpRanges)]" --output table
```
```
# ===== 실행 결과 요약 (2026-10-28) =====
OS · 버전     Amazon Linux 2023 · Grafana 13.2.4 · nginx 1.30.5
실행 중       amazon-ssm-agent · grafana-server · nginx
지표 저장     victoria-metrics-prod -storageDataPath=/victoria-metrics-data -retentionPeriod=14d
nginx         listen 443 ssl · location = /api/v1/write · auth_basic "remote_write" · proxy_pass http://127.0.0.1:8428
보안 그룹     tcp 3000 (2) · tcp 443 (2)
```

Grafana 화면: 데이터 소스 → CloudWatch → **Save & test** 초록색 (10/27), Explore 에서 `cloudwatch` 를 골라 EC2 지표가 그려지면 정상.

## 5. 막혔던 점

| 증상 | 원인 · 해결 |
|---|---|
| CloudWatch 데이터 소스 Save & test 에서 지표는 OK, 로그만 `logs:DescribeLogGroups` 거부 | 로그 그룹 목록 권한이 없었음 → `chargeops-grafana-loggroups-list` (목록만) 추가 후 초록색 |
| Explore 에 Prometheus 가 기본으로 열림 | 위쪽 데이터 소스 선택에서 `cloudwatch` 로 바꿈 |
| DB 패널 19개가 처음엔 비어 있음 | AWS 에서 온프렘 DB 로 가는 길이 없었음 → VPN(WireGuard) 연결 · 읽기 전용 계정 `grafana_aws` 로 해결 (세부는 팀 내부 실행표) |
| 끊김 시험을 보안 그룹 규칙 삭제로 하면 안 끊김 | 이미 맺은 연결은 유지됨 → nginx 정지 · 재시작으로 시험 |

## 6. 비용 (서울, 근사치 — 청구서로 확인)

| 무엇 | 언제 | 대략 |
|---|---|---|
| EC2 t3.small · 디스크 · 고정 공인 IP | 상시 | 월 20~25달러 |
| CloudWatch 데이터 소스 조회 | 볼 때만 | 지표 1,000개 조회당 0.01달러 — 새로고침 간격을 길게 |

## 7. 다음

- CloudWatch 대시보드는 만들지 않음 (데이터 소스 연결까지만)
- 프로젝트 끝: EC2 · 고정 공인 IP · 보안 그룹 · 역할 정리 (EC2 를 지워도 고정 공인 IP 는 남아 요금이 나옴)
