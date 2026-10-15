#!/usr/bin/env python3
"""mgmt 생존 신호 → CloudWatch (ChargeOps/Heartbeat, Host=mgmt) — 1분마다 cron 으로
   5분 동안 안 오면 AWS 경보(chargeops-mgmt-heartbeat) → SNS → 메일 · Slack  (infra/terraform/alerts)
   키: ~/.chargeops.env 의 AWS_HB_KEY_ID · AWS_HB_SECRET (사용자 chargeops-heartbeat — 이 지표 쓰기만)
   cron: * * * * * bash -c 'source ~/.chargeops.env && python3 <저장소>/infra/terraform/alerts/aws-heartbeat.py' >/dev/null 2>&1
   한 번 확인: bash -c 'source ~/.chargeops.env && python3 <저장소>/infra/terraform/alerts/aws-heartbeat.py --verbose'
"""
import os, sys

# ~/.chargeops.env 의 MinIO 설정(사설 CA · 주소)이 AWS 호출에 섞이지 않게 — 섞이면 CERTIFICATE_VERIFY_FAILED (10/15 실측)
for v in ("AWS_CA_BUNDLE", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3", "S3_ENDPOINT_URL", "AWS_PROFILE", "AWS_SESSION_TOKEN"):
    os.environ.pop(v, None)

try:
    import boto3
except ImportError:
    sys.exit("boto3 없음 — python3 -m pip install --user boto3")

key, secret = os.environ.get("AWS_HB_KEY_ID"), os.environ.get("AWS_HB_SECRET")
if not key or not secret:
    sys.exit("AWS_HB_KEY_ID · AWS_HB_SECRET 가 ~/.chargeops.env 에 없음")
cw = boto3.client("cloudwatch", region_name=os.environ.get("AWS_HB_REGION", "ap-northeast-2"),
                  aws_access_key_id=key, aws_secret_access_key=secret)
cw.put_metric_data(Namespace="ChargeOps",
                   MetricData=[{"MetricName": "Heartbeat", "Dimensions": [{"Name": "Host", "Value": "mgmt"}],
                                "Value": 1, "Unit": "Count"}])
if "--verbose" in sys.argv:
    print("보냄: ChargeOps/Heartbeat Host=mgmt 1")
