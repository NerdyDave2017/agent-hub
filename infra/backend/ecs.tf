resource "aws_ecs_cluster" "main" {
  name = "${var.stack_name}-agent-hub"
}

resource "aws_cloudwatch_log_group" "hub" {
  name              = "/ecs/${var.stack_name}-hub"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "hub" {
  family                   = "${var.stack_name}-hub"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.hub_task.arn

  container_definitions = jsonencode([
    {
      name      = "hub"
      image     = var.hub_image
      essential = true
      portMappings = [
        {
          containerPort = var.hub_container_port
          hostPort       = var.hub_container_port
          protocol       = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.hub.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "hub"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "hub" {
  name            = "${var.stack_name}-hub"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.hub.arn
  desired_count   = var.hub_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [aws_security_group.ecs_hub.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.hub.arn
    container_name   = "hub"
    container_port   = var.hub_container_port
  }

  depends_on = [aws_lb_listener.http]
}
