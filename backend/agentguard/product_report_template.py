"""Stable presentation contract for product evaluation reports.

The template owns structure, labels, and visual copy only. It must not contain
an evaluated component, scenario, result, or recommendation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReportSectionId = Literal[
    "capability_overview",
    "evaluation_context",
    "executive_summary",
    "evaluation_dimensions",
    "experiment_overview",
    "experiment_analysis",
    "scenario_stability",
    "product_impact",
    "recommendation",
    "limitations",
    "evidence",
    "technical_metadata",
]
SidebarPanelId = Literal["product_evidence", "experiment_evidence", "technical_evidence"]

REPORT_SECTION_ORDER: tuple[ReportSectionId, ...] = (
    "capability_overview",
    "evaluation_context",
    "executive_summary",
    "evaluation_dimensions",
    "experiment_overview",
    "experiment_analysis",
    "scenario_stability",
    "product_impact",
    "recommendation",
    "limitations",
    "evidence",
    "technical_metadata",
)


class ReportSectionTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: ReportSectionId
    eyebrow: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)


class ReportSidebarPanelTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    panel_id: SidebarPanelId
    eyebrow: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=240)


class ProductReportTemplate(BaseModel):
    """A reusable structure shared by Skill, Tool, Memory, Prompt, and Release reports."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.product-report-template.v1"] = "aig.product-report-template.v1"
    template_id: str = Field(min_length=1, max_length=120)
    locale: str = Field(min_length=2, max_length=20)
    brand_name: str = Field(min_length=1, max_length=120)
    report_label: str = Field(min_length=1, max_length=120)
    title_format: str = Field(min_length=1, max_length=160)
    sections: list[ReportSectionTemplate] = Field(min_length=12, max_length=12)
    dimensions_eyebrow: str = Field(min_length=1, max_length=80)
    dimensions_title: str = Field(min_length=1, max_length=120)
    sidebar_panels: list[ReportSidebarPanelTemplate] = Field(min_length=3, max_length=3)
    breadcrumb_prefix: str = Field(min_length=1, max_length=80)
    language_label: str = Field(min_length=1, max_length=80)
    limited_scenario_notice_format: str = Field(min_length=1, max_length=300)
    footer_format: str = Field(min_length=1, max_length=360)
    dimension_labels: dict[str, str] = Field(min_length=4, max_length=9)
    status_labels: dict[str, str] = Field(min_length=5, max_length=5)
    labels: dict[str, str] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_structure(self) -> "ProductReportTemplate":
        section_ids = tuple(section.section_id for section in self.sections)
        if section_ids != REPORT_SECTION_ORDER:
            raise ValueError(
                "Product report template sections must preserve the stable product-report order: "
                f"{REPORT_SECTION_ORDER}."
            )
        panel_ids = tuple(panel.panel_id for panel in self.sidebar_panels)
        if panel_ids != ("product_evidence", "experiment_evidence", "technical_evidence"):
            raise ValueError("Product report template sidebar panels must preserve the evidence explorer order.")
        if "{skill_name}" not in self.title_format:
            raise ValueError("Product report template title_format must contain {skill_name}.")
        return self

    def section(self, section_id: ReportSectionId) -> ReportSectionTemplate:
        return next(section for section in self.sections if section.section_id == section_id)

    def sidebar(self, panel_id: SidebarPanelId) -> ReportSidebarPanelTemplate:
        return next(panel for panel in self.sidebar_panels if panel.panel_id == panel_id)

    def label(self, key: str, default: str | None = None) -> str:
        if key in self.labels:
            return self.labels[key]
        if default is not None:
            return default
        raise KeyError(f"Product report template has no label for {key!r}.")


def load_product_report_template(path: Path) -> ProductReportTemplate:
    """Load and validate a presentation template without touching report content."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    return ProductReportTemplate.model_validate(payload)


def default_product_report_template() -> ProductReportTemplate:
    path = Path(__file__).parents[2] / "examples" / "report-templates" / "product-evaluation.zh-CN.json"
    return load_product_report_template(path)


__all__ = [
    "ProductReportTemplate",
    "ReportSectionId",
    "ReportSectionTemplate",
    "ReportSidebarPanelTemplate",
    "default_product_report_template",
    "load_product_report_template",
]
