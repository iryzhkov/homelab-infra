# OV 0.5.2 provider integration

Source only. Baseline `stack.yml` still pins OV 0.2.6. Do not merge the provider
fragment into production until the upgrade, reindex and acceptance gates owned
by the root have passed. This directory contains no server or tenant keys.

## Routes and deployment

* PC generation: `http://192.168.90.212:11434/v1`, existing `local:latest`.
* Homelab generation: overlay `http://generation:11434/v1`, `ov-gen-4k`.
* Embedding primary: overlay `http://embedding-adapter:8000/pc/v1/embeddings`.
* Embedding fallback: overlay `http://embedding-adapter:8000/cpu/v1/embeddings`.
* Adapter upstreams are fixed in source. CPU upstream is `http://embeddings:11434/api/embed`.
  The adapter publishes no ports, accepts no URLs or arbitrary prefix, and ignores
  authorization headers. Only members of the provider overlay can reach it.
* The adapter uses the repository's existing pinned Python 3.11 slim image with a
  read-only bind mount, without an image build. Health is local and never infers.

The CPU runtimes are two scoped Compose projects, on homelab and Normandy, attached
to `openviking-providers`. They have no published ports. Both use the pinned
Ollama 0.40.10 image, 2 CPUs, 2 GiB memory, memory-swap=memory, one loaded model,
one parallel request and keepalive=-1. Swarm does not support the required swap
cap and Normandy has active zram. Inspect the resulting HostConfig limits.
The adapter stays in Swarm. OV joins its ordinary network and the provider overlay.

Root rollout commands, after its own deployment approval, from this checkout:

```sh
# Starts only CPU provider projects and installs adapter source. No OV config/data writes.
ansible-playbook playbooks/ov-inference.yml
# Installs the adapter Swarm service and preserves the pinned baseline OV image.
ansible-playbook playbooks/swarm.yml --tags stacks -e only=openviking
# Existing candidate container must join the provider overlay before provider tests.
ssh homelab sudo docker network connect openviking-providers ov-upgrade-052
# Explicit PC-only restart window, after game/operator approval:
ansible-playbook playbooks/ov-pc-coexistence.yml \
  -e ov_pc_apply=true -e ov_pc_allow_restart=true
```

The PC role verifies existing system service owner ollama:ollama, the upstream
0.40.5 runtime, the 65536 generation context and the loaded local model digest.
It requires at least 1536 MiB free VRAM before adding an unloaded embedding model.
If embeddings are already loaded it requires 256 MiB free. These are capacity
guards derived from the measured 1.23 GB embedding allocation, not a reservation
against a game allocating memory concurrently. Failure never unloads a model.
The role adds only `zz-openviking-coexistence.conf`, setting MAX_LOADED_MODELS=2,
preserving the original override.conf and upstream.conf. It retains original
effective settings under root-only `/var/lib/ov-pc-coexistence/service-before.txt`
and restores the prior owned setting if restart verification fails.
It does not upgrade Ollama, drivers, generation aliases, or any desktop setting.
A successful restart naturally removes loaded models. Root must warm the existing
local model explicitly, repeat the read-only preflight, then test embedding and
confirm both remain in /api/ps before enabling PC embedding traffic.

Manual rollback, with restart authority: remove only the owned
`/etc/systemd/system/ollama.service.d/zz-openviking-coexistence.conf`, then
`systemctl daemon-reload` and `systemctl restart ollama`. Retain the rollout
record. Do not replace the existing drop-ins. The downloaded embedding cache
may remain. This playbook is not included in broad host convergence.

## Reprovisioning

`recipes/models.json` pins every reachable blob and both manifests. The embedding
wrapper exactly matches the measured d84267e5 manifest, including the official
ac6da0df manifest and 06507c7b 639150592-byte GGUF. Generation uses the official
0d2a504f manifest and 6acb9bb7 weight. The checked-in Modelfile creates only the
new CPU alias ov-gen-4k. All blobs are checked again before publishing a manifest.
No mutable-tag pull is used. Existing unrelated PC models are retained.

Default file sources reuse the retained measured caches on each host. To rebuild
without them use `-e openviking_embeddings_model_source=registry` and
`-e openviking_generation_model_source=registry`; PC uses `-e ov_pc_model_source=registry`.
All downloads use explicit checksums, including manifest bytes. The adapter fixes
embedding num_ctx=4096 per request on both hosts, independent of the PC's larger
generation default. It sends one text to /api/embed with truncate=false.

## Native configuration and limits

`recipes/providers-052.json` is a nonsecret merge fragment for the candidate and
later controlled cutover. Merge its embedding, vlm and retrieval sections into
the private configuration, preserving root/tenant keys, storage and unrelated
settings. The root owns that operation and the reviewed credential helper.
Do not use embedding metadata override to retain vectors from the old model.
Rebuild the complete vector index and test real-note retrieval first.

Embedding routes are ordered PC, CPU with native failback after 30 seconds or
five fallback requests. Requests specify float format and query/document type.
The exact Qwen retrieval instruction is added once, only for query input.
Already-prefixed queries are rejected. Documents remain plain. Responses require
one finite normalized vector of exactly 1024 floats and valid native usage.
Oversize is HTTP 400, with no fallback or silent truncation; endpoint errors become
502/503/504 so OV's native switcher can try the peer. Busy is 429.
The adapter admits one inference globally and eight HTTP handlers. Request body
is at most 64 KiB, upstream response 128 KiB, input read timeout 10 seconds,
upstream connect timeout 2 seconds, PC read 30 seconds and CPU read 180 seconds.
Health stays responsive during inference.

OV itself estimates tokens and truncates at embedding.max_input_tokens. The
fragment sets this estimate to 65536, above any adapter-accepted body. A string
that OV truncates at that bound still exceeds the adapter's byte limit, so it
cannot become a successful shortened embedding. This is a transport guard, not
a claim that 65536 tokens fit the model. The native 4096-token limit remains
authoritative, including the query instruction and tokenizer special tokens.
The longest tested plain document was 4095 tokens. Oversize errors must be
resolved by explicit splitting/reingestion, not retrying with truncation.

Matched GPU/CPU evidence supplied by the root measured cosine > .99949 and correct
top-1 on three synthetic mixed-host queries. PC queries took 6–28 ms, 2048 tokens
95 ms and 4095 tokens 110 ms, with all 29 layers on GPU and about 1.23 GB VRAM.
Normandy queries took 376–411 ms, 512 tokens 5.66 s, 2048 tokens 35.84 s and
4095 tokens 74.73 s under 2 GiB without swap or OOM. Batch and concurrency above
one are unqualified. Synthetic agreement does not establish real-note quality.

Generation credentials are ordered PC local:latest then homelab ov-gen-4k.
PC uses reasoning_effort=none, presence_penalty=0 and max_tokens=512. CPU alias
uses num_ctx=4096, num_thread=2, num_predict=512 and temperature=0.
Measured CPU summary: 2510 prompt tokens, 112 seconds, peak 1.92 GB, finish=stop.
Measured PC summary: 2584 tokens, 1.64 seconds. CPU is summary-grade; session
structured extraction is not qualified. No global guessed character cap is added
to reduce primary prompts. 12000 semantic characters and 8000 overview characters
were derived trial settings, not token guarantees, especially for Chinese/code.

Generation cutover remains blocked on two concrete native 0.5.2 limitations:

1. VLMConfig.timeout is one positive float shared by all credentials.
   VLMCredential has neither a timeout nor a connect-timeout field.
   OpenAIVLM gives that float to the SDK. Thus timeout=480 permits CPU reads
   but also gives an offline PC a 480-second connect budget. The native test
   forces connection refusal and recovery; it does not claim a blackholed PC
   connection returns quickly. The fragment records 480 honestly.
2. VLMCredential has no prompt-context guard. Ollama generation can truncate
   overlong prompts at its 4096-token CPU context. Completion max_tokens only
   caps output. The measured safe acceptance case is the exact 2510-token
   summary; arbitrary prompts are not qualified.

Proposed minimal next decision for root: approve a narrowly scoped generation
transport in the same internal process, limited to the two fixed chat endpoints,
with a 2-second connect budget and 480-second CPU read budget. CPU requests would
need a verified tokenizer/context guard before forwarding. This is additional
implementation, not a field to invent in the provider JSON. Until approved and
tested, do not advertise general offline generation or enable silent CPU
truncation. A vector-only candidate reindex and raw-query retrieval can proceed.

retrieval.enable_intent=false is a real 0.5.2 field. It avoids optional session
query planning so CPU generation is not required for always-available retrieval.
The CPU summary latency exceeds that step's short deadline. This does not
disable unrelated summary or session extraction calls, and those retain the
generation limitations above.

## Validation

Run through Chaldea on agent-a with HOME asserted to a task-specific .cache
directory before creating TMPDIR under it. Ordinary CI already invokes
scripts/test-openviking-inference.py and installs ansible-core 2.19.13.
The tests exercise the adapter over real loopback HTTP, malformed/bounded requests,
usage/vector validation, context rejection, timeout/connect behavior, busy and
health behavior, and actual Ansible checksum/symlink/idempotence boundaries.

A separate native job pins openviking==0.5.2 and sets OPENVIKING_NATIVE_TEST=1,
running the two NativeOV.test_actual_* cases. Those use the real schema, SDK,
ordered switchers and sync/async failover against controlled HTTP runtimes.
No patched OV fork or mocked native failover implementation is used.
Root should add that job and syntax checks for both new playbooks to its owned
.github/workflows/check.yml. Neither root-owned migration/credential helpers nor
existing documentation were changed by this implementation.
