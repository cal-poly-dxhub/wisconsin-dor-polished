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

- The Bun Javascript runtime (`npm install bun`)
- A UNIX computer with the CDK CLI installed (e.g., via `bun add -g aws-cdk`)
- A target AWS account with the CDK bootstrapped (via `cdk bootstrap`)
- A command line session whose environment points to the target account (see [here](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html))
- A cloned version of this repository
- Docker version 27.4.0
- uv version 0.7.12

### Deployment Steps

The following deployment steps were tested on a t3.large EC2 instance running Ubuntu Server 24.04 LTS with 128 GiB GP3 storage.

Run the kickoff command to install prerequisites: 
```bash
curl -fsSL https://raw.githubusercontent.com/cal-poly-dxhub/wisconsin-dor-polished/dev/scripts/install_ubuntu_noble_24_04.sh | bash
```

Follow the instructions that the kickoff command provides to install the app. These are:

```
newgrp docker 
cd ~/wisconsin-dor-polished 
bun install
bun run deploy
bun run first-time # (also runs customer-side sync script)
```

A DxHub engineer needs to run the source-account data sync script (note to the DxHub engineer: do this in a clone of this repository in which you've run `uv sync`. Be sure the AWS CLI is authenticated with the Wisconsin development account.)

```
uv run scripts/sync_source.py \
  --faq-source-bucket wis-faq-bucket \
  --faq-dest-bucket <FAQ_DEST (from CDK output)> \
  --rag-source-bucket wis-rag-bucket \
  --rag-dest-bucket <RAG_DEST (from CDK output)> \
  --dest-role-arn <ROLE_ARN (from the prior command's output)> \
  --assume-role
```

Sync both the FAQ and RAG knowledge bases (found under Bedrock > Knowledge Bases within the AWS console). (See [this documentation page](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-data-source-sync-ingest.html) for more.)

#### Local Frontend Server

The following is only necessary if you'd like to run a local instance of the application:

Note the template environment file `.env.example` in `packages/webapp`. This file defines environment variables necessary for the local instance of the web app. Use values from the CloudFormation/CDK output to populate these variable definitions:

```
WisconsinBotStack.ApiBaseUrl -> NEXT_PUBLIC_API_BASE_URL
WisconsinBotStack.CognitoUserPoolClientId -> NEXT_PUBLIC_WEBSOCKET_URL
WisconsinBotStack.CognitoUserPoolId -> NEXT_PUBLIC_USER_POOL_ID
WisconsinBotStack.WebSocketUrl -> NEXT_PUBLIC_USER_POOL_CLIENT_ID
```

Rename `packages/webapp/.env.example` to `packages/webapp/.env.local`.

Run a local instance of the frontend at `localhost:xxxx` with: 
```
bun dev
```


