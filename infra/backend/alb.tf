resource "random_id" "tg_suffix" {
  byte_length = 2
}

resource "aws_lb_target_group" "hub" {
  name        = substr("hub-${var.stack_name}-${random_id.tg_suffix.hex}", 0, 32)
  port        = var.hub_container_port
  protocol    = "HTTP"
  vpc_id      = local.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = var.hub_health_check_path
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200-399"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb" "hub" {
  name               = substr("${var.stack_name}-hub-alb", 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = local.subnet_ids

  lifecycle {
    precondition {
      condition     = length(local.subnet_ids) >= 2
      error_message = "Need at least two subnets (two AZs) for the ALB. Set public_subnet_ids or use a default VPC with default subnets per AZ."
    }
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.hub.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.hub.arn
  }
}
