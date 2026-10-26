import pytest

from contract_extractor.models import PageKind
from contract_extractor.stages.page_classify import PageStats, classify_page


@pytest.mark.parametrize(
    ("n_chars", "coverage", "kind"),
    [
        (3000, 0.0, PageKind.NATIVE),
        (50, 0.01, PageKind.NATIVE),  # sparse signature page with a small signature image
        (0, 0.99, PageKind.SCAN),
        (49, 0.71, PageKind.SCAN),
        (120, 0.95, PageKind.HYBRID),
        (3000, 0.70, PageKind.NATIVE),  # 70% is not dominant; must exceed it
        (49, 0.0, PageKind.UNCLASSIFIED),
    ],
)
def test_classify_page(n_chars, coverage, kind):
    assert classify_page(PageStats(number=1, n_chars=n_chars, max_image_coverage=coverage)) is kind
