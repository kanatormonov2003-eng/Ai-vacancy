# AI Lead Hunter KG - remediation progress

Status: P0-1, P0-2, P1-7, P1-8, P0-3 done. P0-4 partly done (see below).
Original note: P0-1 and P0-2 fixed and verified. P1-7 and P1-8 fixed as a side effect
(same file). 42/42 tests green. Everything below was run, not assumed.

## P0-1 Public suffix / domain normalization - FIXED

* NEW `app/domain/publicsuffix.py` - the real PSL algorithm: exact rules,
  wildcard rules, exception rules, ICANN/PRIVATE sections, hosts punycoded
  before matching.
* NEW `app/domain/data/public_suffix_list.dat` - 577-rule snapshot in upstream
  publicsuffix.org format. Replaceable with the full upstream list via the
  PUBLIC_SUFFIX_LIST_PATH env var: refreshing it is an ops action, not a code
  change.
* registrable_domain() returns None for IP literals, bare public suffixes and
  shared hosting suffixes instead of falling back to the suffix itself. That
  fallback was the false-merge bug.
* Guard for unlisted ccTLDs: a registry-style second label (com, co, gov, ...)
  under an unknown two-letter ccTLD counts as part of the suffix. Worst case is
  a slightly over-long suffix, which can only split leads apart, never merge two
  real companies.

Verified by probe:

    toyota.co.jp          -> toyota.co.jp        (was co.jp)
    sony.co.jp            -> sony.co.jp          (was co.jp)
    example.co.uk         -> example.co.uk
    example.com           -> example.com
    sub.example.com       -> example.com
    shop.alfa.com.kg      -> alfa.com.kg
    blog.alfa.kg          -> alfa.kg
    IDN (punycode)        -> resolves
    www.city.kawasaki.jp  -> city.kawasaki.jp    (wildcard + exception rule)
    co.jp, com, 127.0.0.1, github.io -> None

## P0-2 Social extraction - FIXED

Three independent gates, all of which must pass:

1. exact known profile host (SDK/CDN/API hosts are not in the host table)
2. profile-path validation: reserved words, version segments, asset/script
   suffixes, OAuth and share query params, deep content paths all rejected
3. per-network handle grammar

Script bodies, style bodies and src-like attributes are stripped first, so a
network URL that only appears in a script src or a CSP header is treated as
infrastructure, never as a company profile.

Verified rejected: facebook.com/v2.0/dialog/oauth, facebook.com/sdk,
instagram.com/embed.js, instagram.com/oauth, connect.facebook.net SDK script
tags, facebook.com/sharer, facebook.com/tr tracking pixels,
instagram.com/static/*.js, t.me/share/url, facebook.com/login.php,
instagram.com/p/<post>, t.me/+invite, graph.facebook.com/me,
instagram.com/accounts/login/.

Verified accepted: facebook.com/company, instagram.com/company,
instagram.com/company/, t.me/company, wa.me/996555112233,
facebook.com/profile.php?id=N, instagram.com/cafe.alfa.kg/.

New helpers: social_profiles() returns every validated profile with network,
handle and url; social_handle() validates a bare handle supplied by a source.

## P1-7 Name normalization - FIXED (same file, so done now)

* Token ORDER IS SIGNIFICANT. The old key sorted tokens, so every anagram
  collided and merged unrelated businesses.
* is_token_permutation() detects permutations and name_similarity() caps them
  below the merge threshold: shared vocabulary is weak evidence, identical
  wording in the same order is strong.
* sorted_name_key() is kept as a candidate-lookup aid only, never an identity.
* LEGAL_FORMS no longer strips real trading words (centre, company, firm, group,
  holding). NOISE_TOKENS no longer strips city names.
* Result: Vostok Stroy != Stroy Vostok, Centre of Beauty != Beauty,
  Bishkek Stroy != Stroy.

## P1-8 Phone normalization - FIXED (same file)

* Arbitrary 11-13 digit runs are no longer phone numbers. An international
  number must match a real country calling code AND that country plan's
  national-number length range.
* Explicit confidence contract, consumed by dedupe and later by the API:
  KG published plan 0.95, plausible KG 9-digit 0.75, valid foreign shape 0.8,
  everything else rejected. PHONE_STRONG_CONFIDENCE = 0.9 is the bar for using a
  phone as an identity, so a low-confidence number can never be a strong dedupe
  key.
* phone_kind() added: mobile / landline / foreign / unknown.

## One test changed, deliberately

test_multiword_order_insensitive asserted the P1-7 bug itself: that
"Alfa Stroitel" and "Stroitel Alfa" must produce the same key. That assertion is
exactly what the audit filed as a critical false-merge risk, so the test was
wrong, not the product. Replaced with test_token_order_is_significant, which
pins the correct behaviour and documents the reversal inline.

No other test was touched. No assertion was weakened, nothing was skipped, and
no failing test was deleted.

## P0-3 Tenant isolation - FIXED and verified

The module docstring claimed every tenant query was scoped by org_id. It was not.

* update_search(search_id, patch) had `WHERE id = ?`. Any caller holding a search
  id could rewrite another tenant's row. Signature is now
  update_search(org_id, search_id, patch), scoped in SQL, NotFoundError on miss.
* The same class of hole existed in every child-table helper. lead_facts,
  lead_signals, lead_source_refs, lead_scores, website_analyses and
  search_results have no org_id column, so ownership was simply assumed.
  add_source_ref, upsert_fact, upsert_signal, lead_event, save_score,
  save_website_analysis, latest_* and facts_for/signals_for/sources_for now take
  org_id and verify the parent lead first. Bulk readers intersect the caller's id
  list with the org's own leads instead of trusting it.
* insert_lead(org_id, data): org_id is an argument, not a hopeful dict key. A
  mismatching org_id in the payload is rejected, so it cannot be smuggled in.
* Identity columns (id, org_id, created_at, dedupe_key, user_id) are rejected in
  patch dicts. A correctly scoped UPDATE could otherwise still hand a row to
  another org.
* create_search validates the user and the parent search belong to the org.
* search_leads(search_id=...) joins searches with the org filter, so a foreign
  search id can no longer be used to probe which leads another org collected.
* Cross-tenant access raises NotFoundError, never ForbiddenError: a 403 confirms
  the id exists somewhere else, which is itself a leak.
* db.insert/db.update interpolate identifiers because SQLite has no placeholder
  for them, and insert("leads", caller_dict) forwards caller-built keys. Column
  and table names are now validated against ^[A-Za-z_][A-Za-z0-9_]*$, and
  update() refuses an empty WHERE.

Added repo helpers the pipeline needs and that had no writer at all:
add_search_result, search_result_leads, save_score, save_website_analysis,
soft_delete_search, soft_delete_lead, lead_events, get_org_user.

Proof the fix is real (probe against the pre-fix file, kept side by side):

    OLD code, no org param -> HIJACKED
    NEW code, org B        -> blocked: NotFoundError Search not found
    value now              -> original text

New: tests/test_tenant_isolation.py, 24 tests, two real orgs in one DB.

## P0-4 HTTP directory - query encoding and credentials FIXED

* _url() builds the query with urlencode(). A Cyrillic city no longer raises
  UnicodeEncodeError on the socket write, and "Bishkek&admin=1" stays a single
  value instead of injecting a parameter. The base URL must be absolute http(s).
  page/city/category coming from stale config are dropped.
* Authorization/Cookie/X-API-Key are stripped when a redirect crosses origin
  (scheme, host or port), and stay stripped for the rest of the chain.
* The HTTP cache key now includes a salted digest of the credential headers, so
  an authenticated response is never replayed to an anonymous caller. The digest
  never contains the secret itself.

New: tests/test_http_directory.py, 7 tests.

## Still open, in order

* P0-4 remainder: end-to-end HttpDirectoryProvider integration test against the
  fixture server (the /directory route exists and is still unexercised), and the
  robots.txt-on-API-endpoint question.
* P0-5 email validator + soft-delete re-registration semantics.
* P0-6 the real ingestion pipeline. The repo layer it needs now exists and is
  tenant-safe, which was the blocking dependency.
* Then P1 (TLS trust split, per-host circuit keys, analyzer error contract, cache
  transport fields, PBKDF2 outside the write transaction, wired login rate limit,
  redirect auth stripping is already done, session projection), then P2.

## Test result

    python3 -m unittest discover -s tests -t .
    Ran 73 tests - OK
