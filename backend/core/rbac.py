"""
core/rbac.py — the fixed v1 role/permission catalog.

Per the security architecture assessment (Points 9-10): four built-in
roles, no custom-role editor yet. Permission bundles are hardcoded here
rather than editable through an admin UI -- RolePermission is still a
real table (seeded from BUILTIN_ROLES below at migration time) so a
route handler checks has_permission(user, workspace_id, "manage_budgets")
against real rows, not a role-name string comparison, but there is no
code path yet that lets an admin change what a role can do. That's an
explicitly deferred future-enterprise feature, not an oversight.

view_prompts and export_data are restricted to workspace_admin only,
deliberately narrower than the other manage_* permissions -- these two
gate the two most severe findings in the security audit (the unscoped
audit-export and raw-prompt-payload endpoints), so v1 does not extend
them to governance_manager by default.
"""

PERMISSIONS = (
    "view_dashboard",
    "view_reports",
    "view_people",
    "view_agents",
    "manage_agents",
    "manage_budgets",
    "manage_governance",
    "manage_models",
    "manage_integrations",
    "manage_users",
    "view_prompts",
    "export_data",
    "use_ask_costpilot",
    "enable_agent_control",
)

BUILTIN_ROLES = {
    "workspace_admin": {
        "label": "Workspace Admin",
        "permissions": PERMISSIONS,  # everything
    },
    "governance_manager": {
        "label": "AI / Governance Manager",
        "permissions": (
            "view_dashboard", "view_reports", "view_agents",
            "manage_agents", "manage_budgets", "manage_governance", "manage_models",
            "use_ask_costpilot", "enable_agent_control",
        ),
    },
    "department_manager": {
        "label": "Department Manager",
        "permissions": (
            "view_dashboard", "view_reports", "view_people", "view_agents",
            "manage_agents", "manage_budgets",
            "use_ask_costpilot",
        ),
    },
    "viewer": {
        "label": "Viewer / Executive",
        "permissions": (
            "view_dashboard", "view_reports", "view_people", "view_agents",
            "use_ask_costpilot",
        ),
    },
}
