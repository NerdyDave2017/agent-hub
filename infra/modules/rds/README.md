# `modules/rds`

Placeholder for **RDS PostgreSQL**: subnet group, security group, instance (or Aurora).

**LocalStack:** Postgres runs via Docker Compose locally; this module is not applied in the dev branch.

Wire this module from a production composition root after `modules/vpc` provides private subnets and security group rules for hub (App Runner VPC connector) and worker ECS tasks.
