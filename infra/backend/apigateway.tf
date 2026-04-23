# Central HTTPS entrypoint: clients call execute-api URL; API Gateway HTTP API proxies to the ALB.
# Traffic between API Gateway and the public ALB is HTTP (TLS terminates at API Gateway). For
# stricter models, put the ALB private and use a VPC link integration instead.

resource "aws_apigatewayv2_api" "hub" {
  count = var.enable_http_api_gateway ? 1 : 0

  name          = "${var.stack_name}-hub-gateway"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "hub_alb" {
  count = var.enable_http_api_gateway ? 1 : 0

  api_id                 = aws_apigatewayv2_api.hub[0].id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  integration_uri        = "http://${aws_lb.hub.dns_name}/{proxy}"
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_route" "hub_proxy" {
  count = var.enable_http_api_gateway ? 1 : 0

  api_id    = aws_apigatewayv2_api.hub[0].id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.hub_alb[0].id}"
}

resource "aws_apigatewayv2_route" "hub_root" {
  count = var.enable_http_api_gateway ? 1 : 0

  api_id    = aws_apigatewayv2_api.hub[0].id
  route_key = "ANY /"
  target    = "integrations/${aws_apigatewayv2_integration.hub_alb[0].id}"
}

resource "aws_apigatewayv2_integration" "hub_alb_root" {
  count = var.enable_http_api_gateway ? 1 : 0

  api_id                 = aws_apigatewayv2_api.hub[0].id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  integration_uri        = "http://${aws_lb.hub.dns_name}/"
  payload_format_version = "1.0"
}
