# VAT-Identifier-Discovery
The programme uses a free, limited use API Serper key.

Debate topics

1. Knowing that VAT numbers are 9-digit long and that they have to pass a checksum is a good way of filtering out many invalid numbers before passing them to HMRC. However, there are still many valid combinations that will not match a real registration or won't belong to the right company.
2. To keep the dataset up to date without rebuilding from scratch, we would schedule a regular time to track changes via Companies House updates, run incremental passes on new crawl slices, and re-check existing VAT records against HMRC, focusing first on recently changed companies.
3. To identify if our dataset was wrong at scale, with nothing complete to compare it against, we would look for obvious red flags and sample a small subset. This would involve flagging VAT numbers linked to different, unrelated companies, shared addresses, sudden shifts in HMRC response and do regular manual spot-checks on a sample of confirmed records.
4. We would not use Company Check or other commercial aggregators directly in a paid product due to Terms of Service and commercial licensing restructions.
