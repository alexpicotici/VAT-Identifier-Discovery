import os
import re
import time
from urllib.parse import urljoin, urlparse
import pandas as pd
import requests

SERPER_API_KEY = "1a84b748be0be422b40216ff205094c28bfc6529"

# External service endpoints, client header, and common legal/contact subpage paths
SERPER_URL = "https://google.serper.dev/search"
HMRC_URL = "https://api.service.hmrc.gov.uk/organisations/vat/check-vat-number/lookup/{}"
USER_AGENT = "uk-vat-poc/1.1"
SUBPAGE_PATHS = ("/contact", "/contact-us", "/about", "/about-us", "/terms", "/privacy", "/legal")


def get_serper_key() -> str:
    code_key = str(SERPER_API_KEY or "").strip()
    if code_key and code_key != "PASTE_YOUR_SERPER_API_KEY_HERE":
        return code_key
    return os.getenv("SERPER_API_KEY", "").strip()


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, and drop common corporate/filler tokens for matching."""
    stopwords = {
        "limited", "ltd", "llp", "plc", "uk", "group",
        "holdings", "services", "solutions", "the", "and"
    }
    cleaned = re.sub(r"[^a-z0-9\s]", " ", str(text).lower())
    return " ".join(w for w in cleaned.split() if w not in stopwords)


def is_valid_uk_vat_checksum(vat_9: str) -> bool:
    """Validate a 9-digit UK VAT number using standard and +55 Modulus-97 weights."""
    if not re.fullmatch(r"\d{9}", vat_9):
        return False
    digits = [int(d) for d in vat_9]
    weights = [8, 7, 6, 5, 4, 3, 2]
    weighted_sum = sum(d * w for d, w in zip(digits[:7], weights))
    check_value = int(vat_9[7:])
    # UK Modulus-97 check digits can be evaluated directly or with the +55 offset
    return (
        (weighted_sum + check_value) % 97 == 0
        or (weighted_sum + check_value + 55) % 97 == 0
    )


def clean_html_text(html_or_text: str) -> str:
    """Remove non-content HTML blocks and tags, collapsing whitespace into plain text."""
    text = str(html_or_text)
    # Strip script, style, noscript, svg, and head elements before removing remaining tags
    text = re.sub(r"(?is)<(script|style|noscript|svg|head).*?>.*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def extract_vat_candidates(html_or_text: str) -> list[str]:
    """Extract deduplicated, checksum-valid 9-digit UK VAT candidates near GB/VAT cues."""
    plain = clean_html_text(html_or_text)
    patterns = [
        r"\bGB\s*(\d{3}[\s-]?\d{4}[\s-]?\d{2})\b",
        r"\bVAT\s*(?:reg(?:istration)?\s*(?:no|number|#)?|no\.?|number)[:\s#-]+(?:GB\s*)?(\d{3}[\s-]?\d{4}[\s-]?\d{2})\b",
    ]
    found, seen = [], set()
    for pat in patterns:
        for match in re.findall(pat, plain, flags=re.IGNORECASE):
            digits = re.sub(r"[\s-]+", "", match)
            if digits not in seen and is_valid_uk_vat_checksum(digits):
                seen.add(digits)
                found.append(digits)
    return found


def search_candidate_urls(company_name: str, postcode: str = "") -> list[str]:
    """Query Serper for UK-scoped candidate URLs mentioning the company and VAT/GB."""
    api_key = get_serper_key()
    if not api_key:
        return []

    query = (
        f'"{company_name}" {postcode} ("VAT" OR "GB")'.strip()
        if postcode
        else f'"{company_name}" ("VAT" OR "GB")'
    )
    try:
        resp = requests.post(
            SERPER_URL,
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            json={"q": query, "gl": "uk", "hl": "en", "num": 5},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        # Deduplicate organic links while preserving search rank order
        urls, seen = [], set()
        for item in data.get("organic", []):
            link = str(item.get("link", "")).strip()
            if link and link not in seen:
                seen.add(link)
                urls.append(link)
        return urls[:3]
    except Exception:
        return []


def expand_subpages(base_url: str, max_extra: int = 3) -> list[str]:
    """Build bounded candidate subpage URLs on the origin of base_url."""
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return []
    origin = f"{parsed.scheme}://{parsed.netloc}"
    extra = []
    for path in SUBPAGE_PATHS:
        candidate = urljoin(origin, path)
        if candidate != base_url and candidate not in extra:
            extra.append(candidate)
        if len(extra) >= max_extra:
            break
    return extra


def fetch_page_text(url: str) -> tuple[str, str]:
    """Download page HTML for url and return (body_text, status_label)."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=8,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return "", f"FETCH_HTTP_{resp.status_code}"
        return resp.text, "FETCH_OK"
    except Exception as exc:
        return "", f"FETCH_ERR:{type(exc).__name__}"


def lookup_hmrc_vat(vat_9: str) -> tuple[dict | None, str]:
    """Verify a 9-digit UK VAT number against HMRC's VAT lookup API."""
    try:
        resp = requests.get(
            HMRC_URL.format(vat_9),
            headers={"Accept": "application/vnd.hmrc.1.0+json", "User-Agent": USER_AGENT},
            timeout=8,
        )
        if resp.status_code == 200:
            return resp.json().get("target", {}), "HMRC_HTTP_200"
        return None, f"HMRC_HTTP_{resp.status_code}"
    except Exception as exc:
        return None, f"HMRC_ERR:{type(exc).__name__}"


def match_company(company_name: str, postcode: str, hmrc_target: dict) -> tuple[bool, str]:
    """Check whether HMRC's target name or postcode aligns with the Companies House record."""
    hmrc_name = hmrc_target.get("name", "")
    hmrc_postcode = hmrc_target.get("address", {}).get("postcode", "").replace(" ", "").upper()
    norm_target = normalize_text(company_name)
    norm_hmrc = normalize_text(hmrc_name)
    clean_pc = str(postcode or "").replace(" ", "").upper()
    matched = bool(
        (norm_target and norm_hmrc and (norm_target in norm_hmrc or norm_hmrc in norm_target))
        or (clean_pc and hmrc_postcode and clean_pc == hmrc_postcode)
    )
    return matched, hmrc_name


def run_poc(
    csv_path: str = "sample_1000_companies.csv",
    output_csv: str = "vat_poc_results.csv",
    sample_size: int = 25,
    seed: int = 300,
) -> None:
    """Run the bounded PoC pipeline on a reproducible sample and write CSV results."""
    if not get_serper_key():
        print(f"Script path: {os.path.abspath(__file__)}")
        print("Missing Serper API key: paste it into SERPER_API_KEY at the top of the script.")
        return

    # Load all columns as strings so leading zeros and postcodes stay intact
    df = pd.read_csv(csv_path, dtype=str)
    df.columns = df.columns.str.strip()
    sample = df.sample(n=min(sample_size, len(df)), random_state=seed)

    records = []
    candidates_extracted = 0
    verified_matches = 0
    total = len(sample)

    for idx, (_, row) in enumerate(sample.iterrows(), start=1):
        name = str(row.get("CompanyName", "")).strip()
        postcode = str(row.get("RegAddress.PostCode", "")).strip()

        candidate_urls = search_candidate_urls(name, postcode)
        time.sleep(0.25)

        if not candidate_urls:
            print(f"[{idx}/{total}] {name} -> UNRESOLVED_NO_URL")
            records.append({
                "CompanyNumber": str(row.get("CompanyNumber", "")).strip(),
                "CompanyName": name,
                "PostCode": postcode,
                "CandidateURL": "",
                "VATNumber": "",
                "Status": "UNRESOLVED_NO_URL",
                "HMRCName": "",
            })
            continue

        # Combine search hits with a small set of likely contact/legal subpages on the top hit
        urls_to_check = list(candidate_urls)
        urls_to_check.extend(expand_subpages(candidate_urls[0], max_extra=2))

        row_logged = False
        for url in urls_to_check:
            html, _ = fetch_page_text(url)
            time.sleep(0.25)
            if not html:
                continue

            vats = extract_vat_candidates(html)
            if not vats:
                continue

            for vat in vats:
                candidates_extracted += 1
                target, hmrc_status = lookup_hmrc_vat(vat)
                time.sleep(0.25)
                if not target:
                    print(f"[{idx}/{total}] {name} -> GB{vat} ({hmrc_status})")
                    records.append({
                        "CompanyNumber": str(row.get("CompanyNumber", "")).strip(),
                        "CompanyName": name,
                        "PostCode": postcode,
                        "CandidateURL": url,
                        "VATNumber": f"GB{vat}",
                        "Status": hmrc_status,
                        "HMRCName": "",
                    })
                    row_logged = True
                    continue

                matched, hmrc_name = match_company(name, postcode, target)
                if matched:
                    verified_matches += 1
                status = "VERIFIED_MATCH" if matched else "HMRC_NAME_POSTCODE_MISMATCH"
                print(f"[{idx}/{total}] {name} -> GB{vat} ({status})")
                records.append({
                    "CompanyNumber": str(row.get("CompanyNumber", "")).strip(),
                    "CompanyName": name,
                    "PostCode": postcode,
                    "CandidateURL": url,
                    "VATNumber": f"GB{vat}",
                    "Status": status,
                    "HMRCName": hmrc_name,
                })
                row_logged = True

            # Stop scanning additional URLs for this company once a candidate row is recorded
            if row_logged:
                break

        if not row_logged:
            print(f"[{idx}/{total}] {name} -> NO_VAT_FOUND ({candidate_urls[0]})")
            records.append({
                "CompanyNumber": str(row.get("CompanyNumber", "")).strip(),
                "CompanyName": name,
                "PostCode": postcode,
                "CandidateURL": candidate_urls[0],
                "VATNumber": "",
                "Status": "NO_VAT_FOUND",
                "HMRCName": "",
            })

    out_df = pd.DataFrame(records)
    out_df.to_csv(output_csv, index=False)

    # Guard against division by zero when no candidates pass extraction/checksum filtering
    fp_display = (
        f"{((candidates_extracted - verified_matches) / candidates_extracted):.1%}"
        if candidates_extracted
        else "N/A (0 candidates extracted)"
    )

    print(f"\nSample size: {total} | Rows logged: {len(out_df)} | Saved: {output_csv}")
    print(f"Modulus-97 candidates extracted: {candidates_extracted}")
    print(f"HMRC + CH verified matches: {verified_matches}")
    print(f"Candidate false-positive rate: {fp_display}")
    if not out_df.empty:
        print(out_df["Status"].value_counts())


if __name__ == "__main__":
    run_poc()