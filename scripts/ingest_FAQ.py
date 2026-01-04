import json
import boto3
import argparse
import botocore

def ensure_bucket_exists(s3_client, bucket_name: str):
    """Fail if bucket does not exist or is not accessible."""
    try:
        s3_client.head_bucket(Bucket=bucket_name)
    except botocore.exceptions.ClientError as e:
        raise RuntimeError(
            f"Bucket '{bucket_name}' does not exist or is not accessible.\n"
            f"Use the exact bucket name created by CDK.\n"
            f"Details: {e}"
        )

def upload_faq_files(bucket_name: str, faq_json_path: str, prefix: str = ""):
    with open(faq_json_path, "r") as f:
        faqs = json.load(f)

    if not isinstance(faqs, list):
        raise ValueError("FAQ JSON must be a list of {Q, A} objects.")

    s3 = session.client("s3")
    ensure_bucket_exists(s3, bucket_name, region)

    uploaded = 0

    for i, faq in enumerate(faqs, start=1):
        question = str(faq.get("Q", "")).strip()
        answer = str(faq.get("A", "")).strip()

        if not question or not answer:
            print(f"Skipping FAQ #{i} — missing Q or A.")
            continue

        content = f"Q: {question}\nA: {answer}\n"
        filename = f"faq{i}.txt"
        s3_key = f"{prefix}{filename}"

        s3.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=content.encode("utf-8"),
            ContentType="text/plain",
        )

        print(f"Uploaded: s3://{bucket_name}/{s3_key}")
        uploaded += 1

    print(f"\n✅ Uploaded {uploaded} FAQs successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Split a JSON FAQ file and upload each FAQ to S3."
    )
    parser.add_argument("--bucket", required=True, help="S3 bucket name (from CDK output)")
    parser.add_argument("--file", required=True, help="Path to the FAQ JSON file")
    args = parser.parse_args()

    session = boto3.session.Session()
    region = session.region_name

    upload_faq_files(args.bucket, args.file)