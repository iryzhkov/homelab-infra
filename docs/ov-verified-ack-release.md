# OV verified ACK backend release

This file-validated source binding selects backend source
e60c92e22ac924a864a5a3c7699538ff8955fb36, product tree
8d3ade7c0b557150d20c935971c7fe9a4a860d19. Root reports source PR9
reviewed, green and merged. This lane does not independently repeat that review
or CI. The previous 1e3b/625cdec binding is
[historical](ov-curator-backend-qualified-release.md).

| Artifact field | Accepted value |
| --- | --- |
| Docker config ID | sha256:48b046b5c2b43814fb091a1fa2cf60920af07f443679960a60e4e826b0a68576 |
| Distribution manifest | sha256:9924e37b61992a9e5741b229b8c34a1359fab85c4a47716845892ec8f2fb9431 |
| Assembly job | 69566baa-db8e-4428-8c3f-6dd399753057 |
| Patch archive SHA256 | 00f8e32e85f3b89438192c334254b33de26440b98d68523c0facde73f2a40dad |
| Patch archive bytes | 19179520 |
| Custody manifest SHA256 | ee8b7af5889eff9c809101f7acc8e07d0292e1b0257ec6da87e2c78c9dc44e13 |
| Unchanged native SHA256 | 8be7869a9f326ccd6cb67e093216f74296d1e98e93a73290918e6f33744ea1ad |

Authoritative records are
/home/igor/.cache/exact-directory-repair/ack-backend-artifact-04/receipt.md
and returned-artifact-verification.json in that directory. They record independent
PASS for all 915 product files, 938 overlay files, 14 ordered RootFS entries,
21 bundle members, 20 custody entries, complete Config and unchanged native bytes.
The original returned Chaldea patch archive requires retained base blobs and is
not a Docker save. Custody remains pending under the known 4 MiB transfer limit;
no exclusive or durable custody claim follows from the internal inventory.

Committed ACK means processing is unobserved and cleanup is unknown. It does not
prove index completion or a native failure cause. Root owns continuation of any
such operation and must preserve drain without cancellation and cap 32. No pilot,
schedule, native acceptance, hold clearance or live-state claim follows here.

The [combined selection](ov-exact-directory-release.md) now selects the
[lifetime-admission HTTP binding](ov-http-compose.md#explicit-local-config-identity).
The earlier HTTP config
sha256:324b4da5f9aab84f55b517eed9ab4394aca61c92e382583005f4fdd2002126ee,
source b8e678ae07fba3ec49ee142589ea9b61a22f6853, is historical. Root's earlier
HTTP PR15 report described tests only and did not require an image change at
that point. This later source binding selects a new HTTP image without activating
it. The full-resource envelope, personal protocol, native pin and whole-object
guards remain unchanged.

Image staging is a separate parallel lane and remains pending for this binding.
Root owns custody resolution, reviews, infrastructure CI and staging before the
owning CHECK/REAL and identical zero-change repeat. Preserve data/account
lifetimes and unrelated services. No activation occurs in this implementation
lane. Any later activation and runtime qualification remain root-owned.
