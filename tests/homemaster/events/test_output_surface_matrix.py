from __future__ import annotations

import pytest

SURFACES = {
    "cli_text_json_stream": ("canonical_content", "exact"),
    "rich_tool_start": ("canonical_content", "exact"),
    "rich_tool_completion": ("operational_metadata", "bounded_exact"),
    "gateway_public_event": ("canonical_content", "exact"),
    "feishu_business_content": ("canonical_content", "exact"),
    "feishu_sdk_log_and_repr": ("control_plane_secret", "exact"),
    "config_doctor_dry_run_tool": ("control_plane_secret", "exact"),
    "mcp_result_resource_audit": ("canonical_content", "exact"),
    "hook_extension_diagnostic": ("operational_metadata", "exact"),
    "session_memory_trace_benchmark": ("canonical_content", "exact"),
    "invalid_auth_error": ("control_plane_secret", "forbidden"),
    "binary_transport": ("binary_artifact", "opaque_ref"),
    "tenant_session_run_route": ("ownership_reference", "allowlisted"),
    "tracked_repository_material": ("control_plane_secret", "forbidden"),
}


@pytest.mark.parametrize(("surface", "classification_treatment"), SURFACES.items())
def test_locked_output_surface_matrix_is_complete_and_classified(
    surface: str,
    classification_treatment: tuple[str, str],
) -> None:
    classification, treatment = classification_treatment
    assert surface
    assert classification in {
        "canonical_content",
        "control_plane_secret",
        "ownership_reference",
        "binary_artifact",
        "operational_metadata",
    }
    assert treatment in {"exact", "bounded_exact", "forbidden", "opaque_ref", "allowlisted"}
