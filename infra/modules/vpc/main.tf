# VPC with public + private subnets.
# App Runner uses a VPC connector to reach private subnets (for RDS).
# ECS Worker runs in private subnets.
# No ALB needed — App Runner handles HTTPS termination.

locals {
  default_tags = {
    Project     = var.project
    Environment = var.environment
  }
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-vpc" })
}

resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-public-${count.index + 1}" })
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = var.availability_zones[count.index]

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-private-${count.index + 1}" })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.default_tags, { Name = "${var.project}-${var.environment}-igw" })
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(local.default_tags, { Name = "${var.project}-${var.environment}-nat-eip" })
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = merge(local.default_tags, { Name = "${var.project}-${var.environment}-nat" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-public-rt" })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-private-rt" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_vpc_endpoint" "sqs" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.sqs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = merge(local.default_tags, { Name = "${var.project}-${var.environment}-sqs-endpoint" })
}

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = merge(local.default_tags, { Name = "${var.project}-${var.environment}-sm-endpoint" })
}

resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = merge(local.default_tags, { Name = "${var.project}-${var.environment}-ecr-api-endpoint" })
}

resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = merge(local.default_tags, { Name = "${var.project}-${var.environment}-ecr-dkr-endpoint" })
}

resource "aws_vpc_endpoint" "cloudwatch_logs" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true
  tags                = merge(local.default_tags, { Name = "${var.project}-${var.environment}-cw-logs-endpoint" })
}

resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.project}-${var.environment}-vpc-endpoints-sg"
  description = "Allows HTTPS from private subnets to VPC endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from private subnets"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [for s in aws_subnet.private : s.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-vpc-endpoints-sg" })
}

resource "aws_apprunner_vpc_connector" "hub" {
  vpc_connector_name = "${var.project}-${var.environment}-hub-connector"
  subnets            = aws_subnet.private[*].id
  security_groups    = [aws_security_group.app_runner_connector.id]
  tags               = merge(local.default_tags, { Name = "${var.project}-${var.environment}-hub-vpc-connector" })
}

resource "aws_security_group" "app_runner_connector" {
  name        = "${var.project}-${var.environment}-app-runner-connector-sg"
  description = "App Runner VPC Connector — hub outbound to RDS only"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "PostgreSQL to RDS"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [for s in aws_subnet.private : s.cidr_block]
  }

  egress {
    description = "HTTPS to AWS services via VPC endpoints"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-app-runner-connector-sg" })
}

resource "aws_security_group" "worker" {
  name        = "${var.project}-${var.environment}-worker-sg"
  description = "Worker ECS tasks — no inbound HTTP, outbound to RDS + AWS services"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "PostgreSQL to RDS"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [for s in aws_subnet.private : s.cidr_block]
  }

  egress {
    description = "HTTPS to SQS, Secrets Manager, ECR, App Runner agents, external"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-worker-sg" })
}

resource "aws_security_group" "rds" {
  name        = "${var.project}-${var.environment}-rds-sg"
  description = "RDS Postgres — accepts connections from worker and App Runner connector"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from worker"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.worker.id]
  }

  ingress {
    description     = "PostgreSQL from App Runner VPC connector (hub)"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app_runner_connector.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.default_tags, { Name = "${var.project}-${var.environment}-rds-sg" })
}
