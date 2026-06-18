import io
import base64
import json
from typing import List, Dict
from PIL import Image
from textractor.entities.document import Document


def encode_image_to_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def extract_flowcharts_from_document(
    document: Document, bedrock_runtime, doc_id: str
) -> List[Dict]:
    """
    Extract and describe flowcharts (LAYOUT_FIGURE) using Claude via Bedrock.
    Requires Textractor document to be loaded with save_image=True.
    """
    print(f"📐  Extracting flowcharts from document {doc_id}...")
    flowchart_chunks = []

    for page_idx, page in enumerate(document.pages):
        if not hasattr(page, "image") or page.image is None:
            print(
                f"⚠️  No image found for page {page_idx + 1}, skipping flowchart detection."
            )
            continue

        figures = (
            page.page_layout.figures
            if page.page_layout and page.page_layout.figures
            else []
        )

        for i, fig in enumerate(figures):
            bbox = fig.bbox
            img = page.image
            w, h = img.width, img.height

            left = int(bbox.x * w)
            top = int(bbox.y * h)
            right = int((bbox.x + bbox.width) * w)
            bottom = int((bbox.y + bbox.height) * h)

            if (right - left) < 300 or (bottom - top) < 300:
                continue

            cropped = img.crop((left, top, right, bottom))
            image_b64 = encode_image_to_base64(cropped)

            prompt = """You are given an image.  

            1. Read all text from the flowchart, including decision diamonds, process steps, and stop points.
            2. Convert the flowchart into a step-by-step text description of the process. 
            3. Use the format: 
            - Start 
            - Step X → Next Step [condition if any]
            - Stop / Exemptions
            4. Preserve statutory references (e.g., sec. 70.111(19)(a), Wis. Stats.) exactly as written.
            5. Be concise but complete, so that the text can be stored as a knowledge base chunk for retrieval.

            Output your answer in strict JSON format only:

            {
            "flowchart": true,
            "text": "step-by-step process here"
            }

            If the image is NOT a flowchart, respond in the following JSON format:

            {
            "flowchart": false,
            "text": ""
            }

            Do not add any explanations, commentary, or text outside the JSON."""

            body = json.dumps(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": image_b64,
                                    },
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                    "max_tokens": 1000,
                    "temperature": 0.3,
                    "top_p": 0.9,
                    "anthropic_version": "bedrock-2023-05-31",
                }
            )

            response = bedrock_runtime.invoke_model(
                modelId="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                body=body,
                contentType="application/json",
            )

            result = json.loads(response["body"].read())
            flowchart_text = result["content"][0]["text"]
            flowchart_text = json.loads(flowchart_text)

            if flowchart_text.get("flowchart") is True:
                print(flowchart_text)
                flowchart_chunks.append(
                    {
                        "text": flowchart_text.get("text", ""),
                        "metadata": {
                            "doc_id": doc_id,
                            "page_start": page_idx + 1,
                            "page_end": page_idx + 1,
                            "section": "Flowchart",
                            "hierarchy": "Flowchart",
                        },
                    }
                )

    return flowchart_chunks