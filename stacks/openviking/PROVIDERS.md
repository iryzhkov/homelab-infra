# OV 0.5.2 provider integration

This source sets the default OV image in `stack.yml` and `embeddings-prepare.yml`
to the qualified official OV 0.5.2 linux/amd64 image,
`ghcr.io/volcengine/openviking@sha256:2ae2799dac1e606218d9ffeb06af3b65c8218b3556555620a369ed81b7c5aa1f`.
It is prepared for root's controlled activation and does not record that
production has been activated. Until root completes that cutover, production
remains on OV 0.2.6 with its existing cloud providers.

Merge this source only inside root's controlled cutover. After the merge, any
routine Swarm convergence deploys the 0.5.2 default, so the fresh
production-derived migration, fresh tenant credentials and client rollover must
already be in place. Never promote bootstrap, admin or auth keys from the old
`ov-upgrade-052` candidate or any other staging candidate into production. This
directory contains no server or tenant keys.

## Activation evidence

Root supplied this evidence from the fresh accepted offline migration:

* 881/881 records are raw-exact and all 1915 original files are unchanged.
* Ordinary reads as the `default/default` identity with role=user returned all 881
  records exactly.
* CPU and GPU query embeddings returned identical ordered search results (32
  compared). Known-answer retrieval was 9/14 at top-1 and 14/14 within top-5.
* Four structural summaries are absent from the debug scroll listing, but
  authorized content reads of them returned HTTP 200 with exact content.

## Routes and deployment

* PC generation: `http://inference-adapter:8000/pc/v1/chat/completions`, `local:latest`.
* CPU generation: `http://inference-adapter:8000/cpu/v1/chat/completions`, `ov-gen-4k`.
* Embeddings: overlay `http://embedding-adapter:8000/cpu/v1/embeddings`, Normandy CPU
  only. There is no PC embedding route in production.
* `http://embedding-adapter:8000/pc/v1/embeddings` answers HTTP 410
  `embedding_route_disabled` without contacting the PC, so a stale configuration
  fails clearly instead of mixing vectors (see "Why embeddings are CPU only").
* `embedding-adapter` remains a network alias for the renamed `inference-adapter` service.
* Upstreams are fixed in source. Embeddings use `/api/embed` on `embeddings:11434`
  only. Chat uses `/v1/chat/completions` on the existing PC and `generation:11434`. Caller URLs, redirects and arbitrary routes are unsupported.
  The adapter publishes no ports, accepts no URLs or arbitrary prefix, and ignores
  authorization headers. Only members of the provider overlay can reach it.
* The adapter process uses the same pinned official OV 0.5.2 image as OV, a read-only
  source mount and checksummed tokenizer assets. It does not start OV or build an
  image. Its entrypoint is Python, with no inherited OV entrypoint. Health is local
  and never infers. Resource limits are 384 MiB, 0.5 CPU and 32 PIDs.

Only the explicit `playbooks/ov-inference.yml` provisions the provider network and
CPU runtimes. Swarm prepare installs adapter/tokenizer assets under
`/srv/swarm/openviking/inference` and wires stack environment variables. It never
starts or recreates a CPU runtime. Existing matching generation aliases are
retained; missing or drifted aliases are recreated only by that explicit playbook.

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
# Controlled activation only, after migration, credential and client readiness.
# Deploys the OV 0.5.2 default, which joins the provider overlay, and the adapter.
# Does not provision or recreate CPU runtimes or create their network.
ansible-playbook playbooks/swarm.yml --tags stacks -e only=openviking \
  -e swarm_lint_via_chaldea=true
# Experimental and unused in production, since PC embeddings are disabled.
# Explicit PC-only restart window, after game/operator approval:
ansible-playbook playbooks/ov-pc-coexistence.yml \
  -e ov_pc_apply=true -e ov_pc_allow_restart=true
```

## Rollback

Rollback restores a matched set, never the old image alone. Before activation,
confirm that the old image
`git.ryzhkov.dev/igor/openviking@sha256:e9a7222f1f2d4b43bb6e76c50c8a71b70be5d96e7ed0a4a515c1920dcda69b3f`
is still present on homelab.

1. Stop OV 0.5.2 and stop writes, under root's outage procedure.
2. Restore the cold OV 0.2.6 data and configuration captured for this cutover,
   unchanged. Retain the 0.5.2-mutated data for diagnosis. Never open it with
   the 0.2.6 server.
3. Retain the protected fresh ROOT issued for this rollout in the restored
   configuration. It must match the retained pre-cutover client bindings. Never
   re-enable the older public revoked ROOT or rotate again as a rollback shortcut.
4. Revert this activation commit, so that routine convergence keeps 0.2.6. An
   `openviking_image` extra var alone is undone by the next routine run, which
   would start 0.5.2 against 0.2.6 data.
5. Deploy the old image with the matching data:

```sh
ansible-playbook playbooks/swarm.yml --tags stacks -e only=openviking \
  -e swarm_lint_via_chaldea=true \
  -e openviking_image=git.ryzhkov.dev/igor/openviking@sha256:e9a7222f1f2d4b43bb6e76c50c8a71b70be5d96e7ed0a4a515c1920dcda69b3f
```

Client credential rollback follows `docs/ov-client-credentials.md`. Never point a
new 0.5.2 tenant key at 0.2.6 and call that rollback. Root handles any writes
accepted after activation.

## Experimental PC coexistence

The PC coexistence role is experimental and unused: production sends no
embedding traffic to the PC, and nothing below changes that. The role does not
change the PC's global KV cache type, which is why its embeddings do not qualify.
The PC role verifies existing system service owner ollama:ollama, the upstream
0.40.5 runtime, the 65536 generation context and the loaded local model digest.
It requires at least 3072 MiB free VRAM before adding an unloaded embedding model.
If embeddings are already loaded it requires 1024 MiB free. These retain the measured
3.4 GiB starting / 1.6 GiB loaded margin and the 1 GiB spike-abort bound. They are
capacity guards, not a reservation against a game allocating memory concurrently. Failure never unloads a model.
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
embedding num_ctx=4096 per request on the CPU runtime. It sends one text to
/api/embed with truncate=false.

## Native configuration and limits

`recipes/providers-052.json` is a nonsecret merge fragment for the controlled
activation. Merge its embedding, vlm and retrieval sections into
the private configuration, preserving root/tenant keys, storage and unrelated
settings. The root owns that operation and the reviewed credential helper.
Do not use embedding metadata override to retain vectors from the old model.
Rebuild the complete vector index and test real-note retrieval first.

Production embeddings use one credential, the Normandy CPU route. There is no
embedding failover: when the CPU runtime is unavailable, embedding and search
fail visibly rather than using vectors from another runtime. The fragment's
failback settings have no effect with one credential. Requests specify float
format and query/document type.
The exact Qwen retrieval instruction is added once, only for query input.
Already-prefixed queries are rejected. Documents remain plain. Responses require
one finite normalized vector of exactly 1024 floats and valid native usage.
Oversize is HTTP 400, with no retry or silent truncation; endpoint errors become
502/503/504 and surface to OV. Busy is 429.
Embeddings and chat share one inference permit and eight HTTP handlers. Request body
is at most 64 KiB, upstream response 128 KiB, input read timeout 10 seconds,
upstream connect timeout 2 seconds and embedding CPU read 180 seconds. Chat read
budgets are specified below.
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

### Why embeddings are CPU only

The matched evidence above used an isolated GPU runtime with an f16 KV cache. The
live PC service runs Ollama 0.40.5 with a global OLLAMA_KV_CACHE_TYPE=q4_0, which
also applies to the embedding runner. Ollama 0.40.5 has no per-request or
per-model override for it, and it silently ignores unknown request options. Live
PC embeddings measured cosine 0.877 against the CPU vectors, so they cannot share
an index with them. Root accepted CPU query search at about 0.3 seconds and
adopted Normandy CPU embeddings as the steady state. PC generation, and the
ordered PC then homelab generation fallback, are unchanged.

Vectors from a matched f16 GPU runtime remain valid for the CPU index, within the
same 4096-token limit. A temporary isolated f16 GPU runtime may therefore be used
only for a fresh migration's bulk re-embedding, and is removed afterwards. No
permanent extra runtime, process or network is added, and the PC's global KV
setting is not changed.

Generation credentials are ordered PC local:latest then homelab ov-gen-4k.
PC uses reasoning_effort=none, presence_penalty=0 and max_tokens=512. CPU alias
uses num_ctx=4096, num_thread=2, num_predict=512 and temperature=0.
Measured CPU summary: 2510 prompt tokens, 112 seconds, peak 1.92 GB, finish=stop.
Measured PC summary: 2584 tokens, 1.64 seconds. CPU is summary-grade; session
structured extraction is not qualified. No global guessed character cap is added
to reduce primary prompts. 12000 semantic characters and 8000 overview characters
were derived trial settings, not token guarantees, especially for Chinese/code.

Generation now uses the internal adapter for both native ordered credentials.
VLMConfig.timeout=490 is the SDK-to-local-adapter budget. The adapter gives each
upstream connection 2 seconds, then PC reads 120 seconds or CPU reads 480 seconds.
A blackholed PC cannot consume the CPU read budget while connecting. No SDK fork,
extra process, or invented per-credential timeout field is used. Root must merge
the new fragment into the production private config at activation; a config still
using direct generation URLs does not acquire these guarantees automatically.

OV 0.5.2 OpenAIVLM calls chat.completions.create without stream in both sync and
async methods. The adapter implements those nonstreaming calls and rejects stream.
It preserves max_tokens at 1..512, temperature=0, PC reasoning_effort=none and
presence_penalty=0. CPU always reserves the full 512-token output budget even when
a caller asks for less. The source-managed CPU alias remains context=4096,
num_predict=512, num_thread=2. Provisioning recreates it and then checks the actual
GGUF, template, absence of hidden SYSTEM/MESSAGE/ADAPTER directives and the exact
parameter set: the four recipe parameters plus the two stops, <|im_start|> and
<|im_end|>, that the pinned base inherits from its params blob f02dd72b...a2d216.
An existing alias with exactly that content is kept; any extra, missing or drifted
parameter recreates it.
A retained measurement-cache alias inspected during this work had num_thread=1;
that cache was not changed. Do not treat that retained alias as deployment proof.

## CPU prompt guard

Ollama 0.40.10 exposes no supported public tokenize endpoint. The adapter instead
loads tokenizers 0.22.2 from the pinned official OV image and the immutable
Qwen/Qwen2-1.5B-Instruct revision
`ba1cf1846d7df0a0591d6c00649f57e798519da8`. No transformers dependency, dynamic
package install, mutable tokenizer download or custom image build is required.

Runtime asset SHA-256 values:

* tokenizer.json, 7028015 bytes:
  `f7c9b2dba4a296b1aa76c16a34b8225c0c118978400d4bb66bff0902d702f5b8`.
* tokenizer_config.json, 1287 bytes:
  `482bd979881423375ca5414e4e0d94cd7c5349dbb17fffd46b4d36d71e62a1bc`.
* ollama-template.txt, the bundled 182-byte generation-template.txt:
  `62fbfd9ed093d6e5ac83190c86eec5369317919f4b149598d2dbb38900e9faef`.

Provisioning downloads the two HF files from that revision with Ansible checksums
and installs the exact pinned Ollama template. Startup independently verifies all
three digests and refuses missing or changed assets. No runtime download occurs.
The template is the base manifest's actual template layer. It has no default
system message or BOS. HF ships an NFC normalizer which Ollama does not apply;
the guard disables that normalizer for counting and preserves caller bytes.
Unmodified HF counting undercounted a decomposed-accent probe by 30 tokens.
That mismatch is corrected rather than hidden by the safety margin. The HF
config's default helpful-assistant message is not inserted, since that would
count a different prompt.

The supported CPU format is an optional initial system message followed by
alternating plain-text user/assistant messages, ending with user. Every message
must have only role/content fields. Empty messages, embedded Qwen control tokens,
tools, multimodal content, response_format and reasoning options fail clearly
with HTTP 400 before CPU forwarding. PC can carry tools and inline image content
within the body bound; remote image URLs are rejected.

Counting includes each role, newline, im_start/im_end token and the final
assistant prefix. The adapter rejects counted_prompt + 512 + 64 > 4096 with a
visible HTTP 413 reporting the measured count and 3520-token accepted limit.
The 64-token margin is explicit protection beyond the measured tokenizer agreement.
It never removes messages, truncates notes, auto-summarizes or applies a semantic
character limit. HTTP's existing 64 KiB JSON body bound remains a transport bound.

Synthetic comparisons against the pinned actual CPU runtime's OpenAI
usage.prompt_tokens/prompt_eval_count matched exactly for plain, system, multi-turn,
Chinese, code and long prose: 10, 29, 47, 1059, 2774 and 2659 tokens respectively.
The mixed-Unicode prompt matches at 1028 tokens after disabling NFC. Thirteen
separate Unicode probes also match, including decomposed accents, Arabic, emoji,
CJK, fullwidth letters and CRLF. These comparisons used one generated token so
they measured prompt ingestion rather than summary quality. The 2774-token code
and 2659-token prose probes took 127.43 and 134.85 seconds on the retained
num_thread=1 alias. Thread count does not change the tokenizer; this does not
qualify num_thread=2 latency.

The isolated actual native OV SDK then used the same restricted adapter process
and CPU runtime: PC unreachable, sync fallback returned OK in 2.20 seconds,
async returned OK in 0.55 seconds, and oversize surfaced as ModelError with code
context_length_exceeded. The fixed generation hostname resolved to the isolated
runtime in a network namespace with no external route. The first separate-bridge
fixture could not connect between its containers; sharing the isolated namespace
removed that harness networking issue without altering adapter destinations.
Health remained responsive. Temporary containers and network were removed.

Ollama was constrained to 2 CPUs, memory=memory-swap=2147483648, parallel=1 and
loaded=1, with OOMKilled=false. Observed cgroup memory.peak was 1772515328 bytes
for Ollama and 92983296 for the adapter; the adapter used the planned unprivileged
UID, read-only mounts, 384 MiB / 0.5 CPU / 32 PID limits. These are short-output
operational probes; the root's prior 512-output summary evidence remains separate.

Synthetic agreement and a conservative margin qualify this restricted template,
not arbitrary model templates or CPU structured-memory extraction. The actual
Ollama source routes and OV SDK call sites are available in
[Ollama routes.go](https://github.com/ollama/ollama/blob/v0.40.10/server/routes.go),
[Ollama template.go](https://github.com/ollama/ollama/blob/v0.40.10/template/template.go)
and [OV OpenAIVLM](https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/models/vlm/backends/openai_vlm.py).

Context errors from the guard or upstream are permanent 400/413 errors. Runtime
unavailability, timeout or invalid/empty successful responses return 502/503/504
to OV's native ordered credentials; busy is 429. No upstream body, caller content
or authorization header is logged or echoed, and authorization is not forwarded.
This process does not follow redirects or serve general completions/proxy routes.

retrieval.enable_intent=false is a real 0.5.2 field. It avoids optional session
query planning so CPU generation is not required for always-available retrieval.
The CPU summary latency exceeds that step's short deadline. This does not
disable unrelated summary or session extraction calls, and those retain the
restricted CPU prompt format and latency limits above.

## Validation

Run through Chaldea on agent-a with HOME asserted to a task-specific .cache
directory before creating TMPDIR under it. Ordinary CI already invokes
scripts/test-openviking-inference.py and installs ansible-core 2.19.13.
The tests exercise the adapter over real loopback HTTP, malformed/bounded requests,
usage/vector validation, context rejection, timeout/connect behavior, busy and
health behavior, and actual Ansible checksum/symlink/idempotence boundaries.

A separate native job pins openviking==0.5.2 and sets OPENVIKING_NATIVE_TEST=1,
running NativeOV, including both NativeOV.test_actual_* cases through adapter
routes against controlled HTTP runtimes, with the real schema and SDK. The
embedding case qualifies the production CPU-only credential, the query prefix,
sync/async error surfacing and the refused PC route. The generation case keeps
ordered PC/CPU sync/async failover and failback. No patched OV fork or mocked
native failover implementation is used.

When the docker CLI is installed, the ordinary run also renders stack.yml with
the real `docker stack config`, which needs no daemon, and checks that the
adapter SHA-256, adapter path and tokenizer path keep their supplied values and
that a missing or empty value refuses to render. Root has added the standalone native job and both
playbook syntax checks in its owned .github/workflows/check.yml. This worker
did not edit root's CI, migration, credential or prior migration documentation.

The ordinary Python CI job must install `tokenizers==0.22.2` alongside
`ansible-core==2.19.13`; the native job should pin the same tokenizer version.
Tests download only the pinned tokenizer assets into the asserted scratch HOME,
or reuse a prepopulated `OPENVIKING_TOKENIZER_DIR` inside that HOME. The native
class remains named `NativeOV`, so root's standalone CI selector is unchanged.

A complete worker check command, from this worktree:

```sh
chaldea exec --host agent-a -- sh -c '
  export HOME="$PWD/.cache/ov-generation-final/home"
  test "$HOME" = "$PWD/.cache/ov-generation-final/home" || exit 90
  export OPENVIKING_INFERENCE_TEST_HOME="$HOME" OPENVIKING_NATIVE_TEST=1
  mkdir -p "$HOME/tmp"
  export TMPDIR="$HOME/tmp"
  uv run --python 3.11 --with openviking==0.5.2 \
    --with ansible-core==2.19.13 --with tokenizers==0.22.2 \
    python3 scripts/test-openviking-inference.py -v'
```
