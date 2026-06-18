import sys
import os

root = os.path.dirname(__file__)
paths = [
    os.path.join(root, "layers"),
    os.path.join(root, "lambdas", "websocket"),
    os.path.join(root, "lambdas", "chat_api"),
    os.path.join(root, "lambdas", "streaming"),
    os.path.join(root, "lambdas", "resource_streaming"),
    os.path.join(root, "lambdas", "citation_resolver"),
    os.path.join(root, "lambdas", "agentic_retrieval"),
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)
