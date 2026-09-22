/**
 * Parser for the `doc:` href grammar the backend writes into inline citation
 * links.
 *
 * Grammar (exactly one optional `ref`):
 *
 *   doc:<primaryId>[#page=<p>][ (&|#) ref=<refId>[#page=<n>] ]
 *
 *   doc:statutes-70                                  single, no page
 *   doc:statutes-70#page=38                          single, with page
 *   doc:case_law-x#page=3&ref=statutes-70#page=12    dual, primary has a page
 *   doc:case_law-x#ref=statutes-70#page=12           dual, primary has no page
 *   doc:case_law-x#ref=statutes-70                   dual, ref has no page
 *
 * The separator before `ref=` is `&` when the primary already opened a
 * fragment with `#page=`, and `#` when it did not — both are accepted here so
 * a malformed-but-obvious href still resolves.
 *
 * The PRIMARY is the document whose words the link label quotes. The REF is
 * the statute / admin-rule section that label *names*. Citation attribution
 * (source cards, per-card citation lists) always follows the PRIMARY — see
 * parse-inline-citations.ts.
 */

export const DOC_HREF_PREFIX = 'doc:';

export interface DocHrefTarget {
  docId: string;
  page?: number;
}

export interface ParsedDocHref extends DocHrefTarget {
  ref?: DocHrefTarget;
}

// `#page=` is allowed to be empty (`#page=`) so a half-written href from an
// older answer still yields a usable doc id instead of nothing.
const DOC_HREF_RE =
  /^doc:([^#&\s]+)(?:#page=(\d*))?(?:[#&]ref=([^#&\s]+)(?:#page=(\d*))?)?$/;

function toPage(raw: string | undefined): number | undefined {
  if (!raw) return undefined;
  const n = parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

/**
 * Parse a `doc:` href into its primary target and optional secondary (`ref`)
 * target. Returns null for anything that is not a `doc:` href.
 *
 * Tolerant by design: a page fragment may be missing on either target, and an
 * href that does not match the grammar exactly still yields the bare doc id
 * (everything up to the first `#`) rather than null, so a persisted answer
 * written by an older backend never renders as dead text.
 */
export function parseDocHref(href: string | undefined | null): ParsedDocHref | null {
  if (!href || !href.startsWith(DOC_HREF_PREFIX)) return null;

  const m = href.match(DOC_HREF_RE);
  if (!m) {
    const rest = href.slice(DOC_HREF_PREFIX.length);
    const docId = rest.split('#')[0];
    return docId ? { docId } : null;
  }

  const [, docId, primaryPage, refId, refPage] = m;
  const parsed: ParsedDocHref = { docId };
  const page = toPage(primaryPage);
  if (page !== undefined) parsed.page = page;
  if (refId) {
    const ref: DocHrefTarget = { docId: refId };
    const rp = toPage(refPage);
    if (rp !== undefined) ref.page = rp;
    parsed.ref = ref;
  }
  return parsed;
}
