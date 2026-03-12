client<llm> ClaudeSonnetBedrock {
  provider aws-bedrock
  options {
    model "us.anthropic.claude-sonnet-4-6"
    region "us-west-2"
    inference_configuration {
      max_tokens 8192
      temperature 0.0
    }
  }
}
