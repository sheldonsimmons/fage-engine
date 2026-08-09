"""
CostPilot universal connector — observe mode, minimal example.

Use this when your own system already made an AI call (its own model
choice, its own provider key) and you just want CostPilot to know it
happened — no prompt is sent, nothing gets routed or pruned. This is the
whole integration: no SDK to install, no auth flow beyond an API key.

Full field reference: GET https://<your-costpilot-host>/api/integrations/contract
"""
import requests

COSTPILOT_BASE_URL = "https://fage-engine-21cb49fe4806.herokuapp.com"


def report_ai_call(
    workspace_id: str,
    platform_name: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float = None,
    user_external_id: str = None,
    user_name: str = None,
    department: str = None,
    work_external_id: str = None,
    work_type: str = "task",
    work_name: str = None,
):
    """Report one already-made AI call to CostPilot. Returns the parsed response."""
    payload = {
        "contract_version": "2026-07-26",
        "mode": "observe",
        "source": {
            "platform": platform_name,
            "workspace_id": workspace_id,
        },
        "usage": {
            "model_name": model_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            **({"cost_usd": cost_usd} if cost_usd is not None else {}),
        },
    }
    if user_external_id:
        payload["actor"] = {
            "external_id": user_external_id,
            "name": user_name,
            "department": department,
        }
    if work_external_id:
        payload["work"] = {
            "external_id": work_external_id,
            "type": work_type,
            "name": work_name or work_external_id,
        }

    response = requests.post(f"{COSTPILOT_BASE_URL}/api/route", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    # This is the entire integration -- copy this call, swap in your own
    # values, and CostPilot has the data.
    result = report_ai_call(
        workspace_id="acme-prod",
        platform_name="Acme Support Tool",
        model_name="gpt-4o-mini",
        input_tokens=1200,
        output_tokens=340,
        cost_usd=0.0021,
        user_external_id="emp-4471",
        user_name="Jamie Lee",
        department="Customer Support",
        work_external_id="TICKET-8842",
        work_type="ticket",
        work_name="Refund request escalation",
    )
    print(result)
