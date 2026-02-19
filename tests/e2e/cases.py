"""E2E test case definitions — derived from upstream 1000genome-workflow spec.

Cases taken from:
  https://github.com/hyperflow-wms/1000genome-workflow/blob/4ac49569/tests/integration/cases.yaml
Skipping cases 1, 2, and 4 (single-population chr22 smoke tests).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class E2ETestCase(BaseModel):
    """Definition of an E2E test case with expected output files."""

    case_id: str
    prompt: str
    expected_files: list[str] = Field(default_factory=list)
    timeout_seconds: int = 1800
    expected_chromosomes: list[str] = Field(default_factory=list)
    expected_populations: list[str] = Field(default_factory=list)


# Case 3: East Asian HLA autoimmune
EAS_HLA_AUTOIMMUNE = E2ETestCase(
    case_id="eas-hla-autoimmune",
    prompt=(
        "Investigate the frequency of known autoimmune-related variants "
        "in the HLA region (chromosome 6) across East Asian populations."
    ),
    expected_chromosomes=["6"],
    expected_populations=["EAS"],
    expected_files=[
        "chr6n.tar.gz",
        "sifted.SIFT.chr6.txt",
        "chr6-EAS.tar.gz",
        "chr6-EAS-freq.tar.gz",
    ],
    timeout_seconds=1800,
)

# Case 5: EUR vs AFR HLA comparison
EUR_AFR_HLA = E2ETestCase(
    case_id="eur-afr-hla",
    prompt=(
        "Compare the frequency of HLA region variants on chromosome 6 "
        "between European (EUR) and African (AFR) populations."
    ),
    expected_chromosomes=["6"],
    expected_populations=["EUR", "AFR"],
    expected_files=[
        "chr6n.tar.gz",
        "sifted.SIFT.chr6.txt",
        "chr6-EUR.tar.gz",
        "chr6-EUR-freq.tar.gz",
        "chr6-AFR.tar.gz",
        "chr6-AFR-freq.tar.gz",
    ],
    timeout_seconds=2400,
)

# Case 6: BRCA1/BRCA2 multi-population breast cancer
BRCA_BREAST_CANCER = E2ETestCase(
    case_id="brca-breast-cancer",
    prompt=(
        "Analyze BRCA1 (chromosome 17) and BRCA2 (chromosome 13) gene variants "
        "across all five super-populations (AFR, AMR, EAS, EUR, SAS) to identify "
        "population-specific patterns in breast cancer susceptibility genes."
    ),
    expected_chromosomes=["13", "17"],
    expected_populations=["AFR", "AMR", "EAS", "EUR", "SAS"],
    expected_files=[
        "chr13n.tar.gz",
        "sifted.SIFT.chr13.txt",
        "chr17n.tar.gz",
        "sifted.SIFT.chr17.txt",
        "chr13-AFR.tar.gz",
        "chr13-AFR-freq.tar.gz",
        "chr13-AMR.tar.gz",
        "chr13-AMR-freq.tar.gz",
        "chr13-EAS.tar.gz",
        "chr13-EAS-freq.tar.gz",
        "chr13-EUR.tar.gz",
        "chr13-EUR-freq.tar.gz",
        "chr13-SAS.tar.gz",
        "chr13-SAS-freq.tar.gz",
        "chr17-AFR.tar.gz",
        "chr17-AFR-freq.tar.gz",
        "chr17-AMR.tar.gz",
        "chr17-AMR-freq.tar.gz",
        "chr17-EAS.tar.gz",
        "chr17-EAS-freq.tar.gz",
        "chr17-EUR.tar.gz",
        "chr17-EUR-freq.tar.gz",
        "chr17-SAS.tar.gz",
        "chr17-SAS-freq.tar.gz",
    ],
    timeout_seconds=3600,
)

# Case 7: BRCA1 British population
BRCA1_GBR = E2ETestCase(
    case_id="brca1-gbr",
    prompt="Analyze BRCA1 gene variants in the British population.",
    expected_chromosomes=["17"],
    expected_populations=["GBR"],
    expected_files=[
        "chr17n.tar.gz",
        "sifted.SIFT.chr17.txt",
        "chr17-GBR.tar.gz",
        "chr17-GBR-freq.tar.gz",
    ],
    timeout_seconds=1800,
)

E2E_CASES = [EAS_HLA_AUTOIMMUNE, EUR_AFR_HLA, BRCA_BREAST_CANCER, BRCA1_GBR]
