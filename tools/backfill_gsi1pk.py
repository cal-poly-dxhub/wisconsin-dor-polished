"""One-time backfill: add gsi1pk='ALL' to existing ChatHistory items.

Run after deploying the timestampIndex GSI:
  AWS_PROFILE=widor AWS_REGION=us-east-1 uv run python tools/backfill_gsi1pk.py

Idempotent — safe to re-run. Items that already have gsi1pk are skipped.
"""

import os

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
TABLE_NAME = os.environ.get(
    "CHAT_HISTORY_TABLE",
    "WisconsinBotGraphRAG-WisconsinSessionsStackNestedStackWisconsinSessionsStackNestedStac-1P3H46X50M51H-ChatHistoryTableA22BA13C-GTH2UH9SGD0W",
)


def main():
    dynamodb = boto3.client("dynamodb", region_name=REGION)
    updated = 0
    skipped = 0
    scan_kwargs = {"TableName": TABLE_NAME, "ProjectionExpression": "queryId, gsi1pk"}

    while True:
        response = dynamodb.scan(**scan_kwargs)
        for item in response.get("Items", []):
            if "gsi1pk" in item:
                skipped += 1
                continue
            query_id = item["queryId"]["S"]
            dynamodb.update_item(
                TableName=TABLE_NAME,
                Key={"queryId": {"S": query_id}},
                UpdateExpression="SET gsi1pk = :val",
                ExpressionAttributeValues={":val": {"S": "ALL"}},
            )
            updated += 1
            if updated % 100 == 0:
                print(f"  updated {updated} items...")

        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    print(f"Done. Updated: {updated}, Skipped (already had gsi1pk): {skipped}")


if __name__ == "__main__":
    main()
