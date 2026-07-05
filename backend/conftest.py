import os
import sys

root = os.path.dirname(__file__)
# Only add shared layers to sys.path. Individual Lambda directories are
# added by their own conftest or test file — adding them here causes
# module name collisions (every Lambda has a main.py).
paths = [
    os.path.join(root, "layers"),
    os.path.join(root, "lambdas", "websocket"),
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)
