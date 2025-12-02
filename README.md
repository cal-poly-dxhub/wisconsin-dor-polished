# Wisconsin DOR Chatbot

This is a chatbot application meant for the Wisconsin Department of Revenue (DOR). It features a NextJS frontend and a CDK-managed backend. This codebase is written primarily in Typescript, with Lambda functions defined in Python. 

## Features

### Key Components 

- A self-contained NextJS interface, paired with the backend via environment variables
- Separate FAQ and document knowledge bases
- A DynamoDB chat history and the ability to ask follow-up questions
- Real-time response streaming via WebSocket
- Step function response flow

### Architecture

<img width="3134" height="1799" alt="image" src="https://github.com/user-attachments/assets/1cb42865-eead-44f4-9032-4b96a3d76cb2" />

## Deployment 

### Prerequisites

- A UNIX computer with the CDK CLI installed (e.g., via `npm install -g aws-cdk`)
- A target AWS account with the CDK bootstrapped (via `cdk bootstrap`)
- A cloned version of this repository
- A command line session whose current directory is a clone of this repository and whose environment points to the deployment target account (see [here](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html))

### Deployment Steps

Install dependencies with: 

```bash
bun install
```

Package Lambda-function code and deploy all resources to the target account using:
```bash
bun run deploy
```

Note the 

Run a local instance of the frontend at `localhost:3000` with: 
```
bun dev
```



