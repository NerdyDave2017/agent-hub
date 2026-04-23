resource "aws_security_group" "alb" {
  name        = "${var.stack_name}-hub-alb"
  description = "Internet-facing ALB for hub"
  vpc_id      = local.vpc_id

  ingress {
    description = "HTTP from clients"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ecs_hub" {
  name        = "${var.stack_name}-hub-ecs"
  description = "Fargate tasks for hub"
  vpc_id      = local.vpc_id

  ingress {
    description     = "Hub port from ALB only"
    from_port       = var.hub_container_port
    to_port         = var.hub_container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
