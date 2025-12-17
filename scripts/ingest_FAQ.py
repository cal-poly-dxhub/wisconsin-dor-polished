import json
import boto3
import os
import argparse

def upload_faq_files(bucket_name, faq_json_path):
    with open(faq_json_path, "r") as f:
        faqs = json.load(f)

    s3 = boto3.client("s3")

    if not isinstance(faqs, list):
        raise ValueError("FAQ JSON must be a list of {Q, A} objects.")

    for i, faq in enumerate(faqs, start=1):
        question = faq.get("Q", "").strip()
        answer = faq.get("A", "").strip()

        if not question or not answer:
            print(f"Skipping FAQ #{i} — missing Q or A.")
            continue

        content = f"Q: {question}\nA: {answer}\n"
        filename = f"faq{i}.txt"
        s3_key = f"{filename}"

        s3.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=content.encode("utf-8"),
            ContentType="text/plain"
        )

        print(f"Uploaded: {s3_key}")

    print("\nAll FAQs uploaded successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split a JSON FAQ file and upload each FAQ to S3.")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--file", required=True, help="Path to the FAQ JSON file")

    args = parser.parse_args()
    upload_faq_files(args.bucket, args.file)