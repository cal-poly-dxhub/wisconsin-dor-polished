client<llm> ClaudeSonnetBedrock {
  provider aws-bedrock
  options {
    model "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
    region "us-west-2"
    inference_configuration {
      max_tokens 2048
      temperature 0.0
      top_p 0.9
    }
  }
}
