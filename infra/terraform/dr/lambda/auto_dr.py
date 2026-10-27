"""자동 DR 발동 (설계도 ⑫: 경보 → Lambda → EC2) — 훈련 모드 고정
   mgmt 생존 신호 경보(chargeops-mgmt-heartbeat)가 ALARM 이 되면, 서버 설계도(Launch Template)로 DR-DB 를 한 대 띄운다.
   DR-DB 는 첫 부팅에 dr-restore.sh 를 훈련 모드(DR_MODE=drill, 저장소에 아무것도 안 올림)로 자동 실행한다.
   안전장치: ① 우리 경보의 ALARM 만 반응 (예산 · OK · 다른 메일은 무시)  ② 이미 DR-DB 가 떠 있으면 또 띄우지 않음
"""
import json, os
import boto3

ec2 = boto3.client("ec2")
sns = boto3.client("sns")
ALARM = os.environ["ALARM_NAME"]
LT_ID = os.environ["LAUNCH_TEMPLATE_ID"]
SUBNET = os.environ["SUBNET_ID"]
TOPIC = os.environ.get("TOPIC_ARN", "")


def _alarm_states(event):
    for rec in event.get("Records", []):
        try:
            msg = json.loads(rec["Sns"]["Message"])
        except (KeyError, ValueError):
            continue                                     # 예산 알림 · 우리 자신의 알림 등 JSON 이 아닌 것
        yield msg.get("AlarmName"), msg.get("NewStateValue")


def handler(event, context):
    if not any(name == ALARM and state == "ALARM" for name, state in _alarm_states(event)):
        return {"action": "ignored"}
    running = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": ["chargeops-dr-db"]},
        {"Name": "instance-state-name", "Values": ["pending", "running"]},
    ])
    ids = [i["InstanceId"] for r in running["Reservations"] for i in r["Instances"]]
    if ids:
        print(f"이미 DR-DB 가 있음 — 띄우지 않음: {ids}")
        return {"action": "already-running", "instances": ids}
    out = ec2.run_instances(
        LaunchTemplate={"LaunchTemplateId": LT_ID, "Version": "$Latest"},
        SubnetId=SUBNET, MinCount=1, MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "chargeops-dr-db"},
            {"Key": "Project", "Value": "chargeops"},
            {"Key": "LaunchedBy", "Value": "auto-dr-lambda"},
        ]}],
    )
    iid = out["Instances"][0]["InstanceId"]
    print(f"자동 DR 발동: {iid}")
    if TOPIC:
        sns.publish(TopicArn=TOPIC, Subject="[ChargeOps] 자동 DR 발동 (훈련 모드)",
                    Message=f"mgmt 생존 신호 경보 → DR-DB {iid} 생성. 약 3~4분 뒤 /var/log/dr-restore.log 에 복원 결과 "
                            f"(Session Manager). 시험이 끝나면 이 서버를 종료할 것.")
    return {"action": "launched", "instance": iid}
