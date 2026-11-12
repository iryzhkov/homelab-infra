#!/usr/bin/env python3
"""Require the dedicated PC runtime to reproduce the stored Normandy CPU canary vector."""
import json
import math
import sys
import urllib.request

port, canary_path = int(sys.argv[1]), sys.argv[2]
canary = json.load(open(canary_path))
request = {"model": canary["model"], "input": canary["text"], "truncate": False, "keep_alive": "30m",
           "options": {"num_ctx": 4096, "num_thread": 2}}
response = urllib.request.urlopen(urllib.request.Request(
    f"http://127.0.0.1:{port}/api/embed", json.dumps(request).encode(),
    {"Content-Type": "application/json"}), timeout=120)
vector = json.load(response)["embeddings"][0]
norm = math.sqrt(sum(value * value for value in vector))
cosine = sum(a * b for a, b in zip(vector, canary["embedding"])) / norm
print(json.dumps({"cosine": round(cosine, 6)}))
sys.exit(0 if cosine >= 0.999 else 1)
