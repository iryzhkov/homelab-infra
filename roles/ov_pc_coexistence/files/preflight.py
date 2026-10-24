#!/usr/bin/env python3
"""Read-only PC capacity/custody check. Never loads, unloads or changes a model."""
import argparse
import json
import subprocess
import urllib.request


def check(models, free_mib):
    local = [m for m in models if m.get("name") == "local:latest"]
    if len(local) != 1 or local[0].get("size_vram", 0) <= 0:
        raise ValueError("local:latest must already be loaded on the GPU; warm it explicitly first")
    embedding = [m for m in models if m.get("name") == "qwen3-embedding:0.6b"]
    required = 256 if embedding else 1536
    if free_mib < required:
        raise ValueError("Insufficient free GPU memory; leave the game and local model alone")
    return {"local_digest": local[0]["digest"], "embedding_loaded": bool(embedding),
            "free_mib": free_mib, "required_free_mib": required}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-local-digest", required=True)
    args = parser.parse_args()
    with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3) as response:
        models = json.load(response)["models"]
    free = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], text=True
    ).splitlines()
    if len(free) != 1:
        raise SystemExit("Expected exactly one NVIDIA GPU")
    try:
        result = check(models, int(free[0]))
        if result["local_digest"] != args.expected_local_digest:
            raise ValueError("local:latest changed; preserve its owner and inspect before continuing")
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result))
