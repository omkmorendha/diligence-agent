"""DiliAgent v1 document-review pipeline (spec sections 5-9).

Stage modules, in pipeline order:
    parse         S1  parse_document(path) -> DocModel
    extract       S2  extract_claims(docmodel, pilot) -> list[Claim]
    registry      S3  corpus_registry() / scope_check(claims) -> list[Claim]
    verify        S4  verify_claims(review_id, claims, trace, workers) -> list[VerificationResult]
    report        S5  assemble_report(review_id, docmodel, claims, results) -> ReviewReport
    annotate_pdf  S6  annotate_pdf(src, report, out)
    annotate_docx S6  annotate_docx(src, report, out)
    annotate_md   S6  annotate_md(src, report, out)
    report_html   S6  render_report_html(report, docmodel) -> str
    run_review        run_review(review_id, upload_path, pilot) -> ReviewReport

The signatures in these modules are FROZEN contracts for the parallel v1 build:
downstream agents implement against them and MUST NOT change them.
"""
