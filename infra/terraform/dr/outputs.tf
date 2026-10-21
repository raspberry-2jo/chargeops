output "vpc_id" {
  value = aws_vpc.dr.id
}

output "builder_instance_id" {
  description = "① 골든 이미지용 임시 서버 — 상태가 stopped 가 되면 ② 로"
  value       = try(aws_instance.builder[0].id, null)
}

output "golden_ami_id" {
  value = local.golden_id
}

output "dr_instance_id" {
  description = "③ DR-DB — 콘솔 EC2 → 이 서버 → 연결 → Session Manager"
  value       = try(aws_instance.dr_db[0].id, null)
}

output "rds_endpoint" {
  value = try(aws_db_instance.compare[0].address, null)
}
