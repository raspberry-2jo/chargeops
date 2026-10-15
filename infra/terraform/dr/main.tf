# ============================================================================
# DR VPC — "들어오는 길 0" 인 사설 망에서, 백업(S3)만으로 DB 를 되살리는 연습장
#   상시(무료)   : VPC · 프라이빗 서브넷 A(2a) · C(2c) · 보안 그룹 · S3 게이트웨이 엔드포인트 · IAM 역할
#   한 번        : 골든 이미지(AMI = PostgreSQL 17 · pgBackRest · SSM 에이전트 · dr-restore.sh 미리 설치) + 서버 설계도(Launch Template)
#   훈련 때만    : SSM 엔드포인트 3개 · DR-DB EC2 (켜지면 자동 복원) · (선택) RDS 비교
#   순서 · 비용 · 확인: docs/setup/aws-dr-vpc.md
# ============================================================================
locals {
  name      = "chargeops-dr"
  az_a      = "${var.region}a"
  az_c      = "${var.region}c"
  repo_key  = trimprefix(var.repo_path, "/") # S3 안의 경로 (앞의 / 없이)
  golden_id = var.make_ami ? aws_ami_from_instance.golden[0].id : null
}

# ── 망 ─────────────────────────────────────────────────────────────────────
resource "aws_vpc" "dr" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true # SSM 엔드포인트의 사설 DNS 에 필요
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.dr.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 1) # 10.20.1.0/24
  availability_zone = local.az_a
  tags              = { Name = "${local.name}-private-a" }
}

resource "aws_subnet" "private_c" {
  vpc_id            = aws_vpc.dr.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 2) # 10.20.2.0/24 — RDS 서브넷 그룹이 AZ 2개를 요구
  availability_zone = local.az_c
  tags              = { Name = "${local.name}-private-c" }
}

# 프라이빗 라우팅: 인터넷으로 나가는 길이 없다 (NAT 없음). S3 는 게이트웨이 엔드포인트로만
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.dr.id
  tags   = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "private_c" {
  subnet_id      = aws_subnet.private_c.id
  route_table_id = aws_route_table.private.id
}

# S3 전용 길 (게이트웨이 엔드포인트 — 무료). 정책으로 "백업 버킷의 우리 경로 읽기"만 통과
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.dr.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "BackupList", Effect = "Allow", Principal = "*", Action = ["s3:ListBucket"],
      Resource = ["arn:aws:s3:::${var.backup_bucket}"] },
      { Sid = "BackupRead", Effect = "Allow", Principal = "*", Action = ["s3:GetObject"],
      Resource = ["arn:aws:s3:::${var.backup_bucket}/${local.repo_key}/*"] }
    ]
  })
  tags = { Name = "${local.name}-s3" }
}

# ── 보안 그룹: 들어오는 규칙 0 ──────────────────────────────────────────────────
resource "aws_security_group" "dr_db" {
  name        = "${local.name}-db"
  description = "DR-DB: no inbound. out 443 to S3 and SSM endpoints, 5432 to RDS"
  vpc_id      = aws_vpc.dr.id
  egress {
    description     = "S3 (gateway endpoint)"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [aws_vpc_endpoint.s3.prefix_list_id]
  }
  egress {
    description = "SSM interface endpoints"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    description = "RDS compare"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  tags = { Name = "${local.name}-db" }
}

resource "aws_security_group" "endpoints" {
  name        = "${local.name}-endpoints"
  description = "SSM endpoints: 443 from inside the VPC only"
  vpc_id      = aws_vpc.dr.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  tags = { Name = "${local.name}-endpoints" }
}

# ── IAM: DR 서버 역할 (키 없음) ─────────────────────────────────────────────────
resource "aws_iam_role" "dr" {
  name = "chargeops-dr-ec2"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

# AWS 도입 보고서 4장 ③ 과 같음: 백업 경로 읽기만 (쓰기 · 지우기 없음 → 훈련본이 저장소를 건드릴 수 없음)
resource "aws_iam_role_policy" "dr_read" {
  name = "chargeops-dr-read"
  role = aws_iam_role.dr.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "ListRepoRoot", Effect = "Allow", Action = ["s3:ListBucket"], Resource = ["arn:aws:s3:::${var.backup_bucket}"],
      Condition = { StringEquals = { "s3:prefix" = ["", local.repo_key], "s3:delimiter" = ["/"] } } },
      { Sid = "ListRepo", Effect = "Allow", Action = ["s3:ListBucket"], Resource = ["arn:aws:s3:::${var.backup_bucket}"],
      Condition = { StringLike = { "s3:prefix" = ["${local.repo_key}/*"] } } },
      { Sid = "RepoReadOnly", Effect = "Allow", Action = ["s3:GetObject"], Resource = ["arn:aws:s3:::${var.backup_bucket}/${local.repo_key}/*"] }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "dr_ssm" {
  role       = aws_iam_role.dr.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" # SSM 세션 접속 (포트 0 으로 들어가기)
}

resource "aws_iam_instance_profile" "dr" {
  name = "chargeops-dr-ec2"
  role = aws_iam_role.dr.name
}

# ── ① 골든 이미지 만들기 (임시 공인 서브넷 · 인터넷 게이트웨이 — 만든 뒤 지움) ─────────────────
data "aws_ami" "rocky" {
  count       = var.build_ami && var.rocky_ami_id == "" ? 1 : 0
  most_recent = true
  owners      = ["792107900819"] # Rocky Linux 공식 (Marketplace 구독 없이 쓰는 공개 이미지)
  filter {
    name   = "name"
    values = ["Rocky-9-EC2-Base-9.*x86_64*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_internet_gateway" "build" {
  count  = var.build_ami ? 1 : 0
  vpc_id = aws_vpc.dr.id
  tags   = { Name = "${local.name}-build-igw" }
}

resource "aws_subnet" "build" {
  count                   = var.build_ami ? 1 : 0
  vpc_id                  = aws_vpc.dr.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 100) # 10.20.100.0/24 — 이미지 만들 때만
  availability_zone       = local.az_a
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-build" }
}

resource "aws_route_table" "build" {
  count  = var.build_ami ? 1 : 0
  vpc_id = aws_vpc.dr.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.build[0].id
  }
  tags = { Name = "${local.name}-build" }
}

resource "aws_route_table_association" "build" {
  count          = var.build_ami ? 1 : 0
  subnet_id      = aws_subnet.build[0].id
  route_table_id = aws_route_table.build[0].id
}

resource "aws_security_group" "build" {
  count       = var.build_ami ? 1 : 0
  name        = "${local.name}-build"
  description = "AMI builder: no inbound, out 80/443 for packages"
  vpc_id      = aws_vpc.dr.id
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "builder" {
  count                                = var.build_ami ? 1 : 0
  ami                                  = var.rocky_ami_id != "" ? var.rocky_ami_id : data.aws_ami.rocky[0].id
  instance_type                        = "t3.small"
  subnet_id                            = aws_subnet.build[0].id
  vpc_security_group_ids               = [aws_security_group.build[0].id]
  instance_initiated_shutdown_behavior = "stop" # 설치가 끝나면 스스로 꺼짐 → 꺼진 것을 보고 ② 로
  user_data = templatefile("${path.module}/scripts/build-golden.sh.tftpl", {
    region = var.region, dr_restore_url = var.dr_restore_url
  })
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }
  tags       = { Name = "${local.name}-ami-builder" }
  depends_on = [aws_route_table_association.build]
}

# ② 골든 이미지. 임시 서버를 지운 뒤에도 이미지는 남도록 원본 서버 id 변화는 무시
resource "aws_ami_from_instance" "golden" {
  count              = var.make_ami ? 1 : 0
  name               = "chargeops-dr-golden-pg17"
  source_instance_id = try(aws_instance.builder[0].id, "")
  lifecycle {
    ignore_changes = [source_instance_id]
    precondition {
      condition     = var.build_ami || try(aws_instance.builder[0].id, "") == ""
      error_message = "처음 이미지를 찍을 때는 build_ami=true 와 make_ami=true 를 같이 (임시 서버가 꺼진 뒤)."
    }
  }
  tags = { Name = "chargeops-dr-golden-pg17" }
}

# 서버 설계도 (Launch Template) — 나중에 자동 DR(람다)도 이것 하나로 서버를 띄운다
resource "aws_launch_template" "dr" {
  count                  = var.make_ami ? 1 : 0
  name                   = "chargeops-dr-db"
  image_id               = local.golden_id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.dr_db.id]
  iam_instance_profile {
    name = aws_iam_instance_profile.dr.name
  }
  metadata_options {
    http_tokens = "required"
  }
  # 디스크는 골든 이미지 그대로 (임시 서버의 20GB gp3)
  user_data = base64encode(templatefile("${path.module}/scripts/dr-boot.sh.tftpl", {
    region = var.region, bucket = var.backup_bucket, repo_path = var.repo_path, auto_restore = var.auto_restore
  }))
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "chargeops-dr-db", Project = "chargeops" }
  }
}

# ── ③ 훈련 때만 ────────────────────────────────────────────────────────────
resource "aws_vpc_endpoint" "ssm" {
  for_each            = var.drill || var.auto_dr ? toset(["ssm", "ssmmessages", "ec2messages"]) : toset([]) # 자동 DR 시험 때도 들어가 보려면 필요
  vpc_id              = aws_vpc.dr.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private_a.id] # 비용: 엔드포인트 x AZ 시간 요금 → 한 AZ 만
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  tags                = { Name = "${local.name}-${each.key}" }
}

resource "aws_instance" "dr_db" {
  count     = var.drill ? 1 : 0
  subnet_id = aws_subnet.private_a.id
  launch_template {
    id      = aws_launch_template.dr[0].id
    version = aws_launch_template.dr[0].latest_version
  }
  lifecycle {
    precondition {
      condition     = var.make_ami
      error_message = "훈련(drill) 전에 골든 이미지가 있어야 한다 — make_ami=true."
    }
  }
  depends_on = [aws_vpc_endpoint.ssm, aws_vpc_endpoint.s3, aws_iam_role_policy.dr_read]
}

# ── ④ (선택) RDS 비교 ────────────────────────────────────────────────────────
resource "aws_security_group" "rds" {
  count       = var.drill && var.enable_rds ? 1 : 0
  name        = "${local.name}-rds"
  description = "RDS compare: 5432 from DR-DB only"
  vpc_id      = aws_vpc.dr.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.dr_db.id]
  }
}

resource "aws_db_subnet_group" "dr" {
  count      = var.drill && var.enable_rds ? 1 : 0
  name       = "chargeops-dr"
  subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_c.id] # RDS 는 AZ 2개 묶음이 필수
}

resource "aws_db_parameter_group" "dr" {
  count  = var.drill && var.enable_rds ? 1 : 0
  name   = "chargeops-dr-pg17"
  family = "postgres17"
  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements,pg_cron,pgaudit" # 덤프에 있는 확장 (pgvector 는 미리 올릴 필요 없음)
    apply_method = "pending-reboot"
  }
  parameter {
    name         = "cron.database_name"
    value        = "chargeops"
    apply_method = "pending-reboot"
  }
}

resource "aws_db_instance" "compare" {
  count                   = var.drill && var.enable_rds ? 1 : 0
  identifier              = "chargeops-dr-compare"
  engine                  = "postgres"
  engine_version          = "17"
  instance_class          = "db.t4g.micro"
  allocated_storage       = 20
  storage_type            = "gp3"
  db_name                 = "chargeops"
  username                = "dba"
  password                = var.rds_password
  db_subnet_group_name    = aws_db_subnet_group.dr[0].name
  parameter_group_name    = aws_db_parameter_group.dr[0].name
  vpc_security_group_ids  = [aws_security_group.rds[0].id]
  availability_zone       = local.az_a
  publicly_accessible     = false
  multi_az                = false
  backup_retention_period = 0 # 비교 실험용 — 자동 백업 없음
  skip_final_snapshot     = true
  deletion_protection     = false
  apply_immediately       = true
  lifecycle {
    precondition {
      condition     = length(var.rds_password) >= 12
      error_message = "enable_rds 때는 TF_VAR_rds_password 를 12자 이상으로 (read -rsp 로 입력)."
    }
  }
}
