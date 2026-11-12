# Concise OV abstracts

The installed OpenViking PromptManager accepts prompts.templates_dir and falls
back to bundled templates for prompt IDs absent from the override directory.
No upstream fork or image patch is required.

Run playbooks/ov-abstracts.yml with inventory/hosts.yml and limit homelab, first
with --check --diff, then real, then an identical real repeat. The playbook
installs three upstream-schema prompt overrides in the existing state mount,
preserves the complete private configuration and an original mode0600 backup,
and selects /app/data/prompt-templates. A changed prompt or selection restarts
only the OpenViking Swarm service to clear its prompt cache. The repeat must
report changed=0. No token or configuration body is printed.

File/document summaries and the first overview paragraph request 2-3 factual
sentences, at most 70 words. Remaining overview navigation and source-link
placeholders retain the upstream structure. Length is a model instruction,
not a guaranteed truncation rule. Existing summaries remain unchanged until
semantic regeneration. Test a small disposable sample first; broad content
regeneration belongs to the scheduled OV curator.
