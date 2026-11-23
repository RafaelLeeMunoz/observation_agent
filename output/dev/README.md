# Output of the `dev` slice: a pipeline test

**Pipeline test on a development slice, not a finding.** These outputs test the pipeline on a ~200-message development slice of the corpus. Its counts, rates, hours and dollars are NOT validated findings about the company: rates are divided by each mailbox's whole date window while the slice holds only a small share of its messages, and no reviewer has checked the claims. The quotes and citations are real and verified against the raw files.

Written by `src/report.py` on every run of the slice.
