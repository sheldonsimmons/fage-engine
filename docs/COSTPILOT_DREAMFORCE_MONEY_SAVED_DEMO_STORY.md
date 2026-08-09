# CostPilot Dreamforce Money Saved Demo Story

## Demo Name

The Routine Request That Should Not Use The Premium Model

## Demo Goal

Show that CostPilot can reduce AI spend by routing simple work to a lower-cost model tier instead of using a premium model for routine work.

The viewer should walk away thinking:

"Not every AI request needs the expensive model."

## The Setup

A support team uses an AI agent to help respond to customer requests. A customer asks for a simple status update that does not need a premium model.

Without CostPilot, simple work like this could still be sent to an expensive model just because the agent is available.

With CostPilot, the request can be recognized as routine, routed to a lower-cost tier, and logged for reporting.

## Fictional Company

Customer: Northstar Home Services

Internal team: Support

AI agent: Support Response Agent

Platform: Salesforce / Service Cloud-style workflow

## Demo Payload

Use this as the customer request or demo CRM description.

```text
Subject: Account access status update

Hi team,

Please write a brief friendly status update for our operations manager. Confirm that the account access request was received, the team is reviewing it, and next steps will follow.

Previous note:
Customer asked if the request is in the queue. Support replied that the request was received and is being reviewed.

System note:
Case updated. Request received. Request received. Queue assigned. Queue assigned. Follow-up needed. Follow-up needed.

Footer: This message came from a customer support system and may repeat in long threads.

Signature:
Jamie Carter
Northstar Home Services

Signature:
Support Team
```

## What CostPilot Should Show

The exact result may vary depending on current model rules, keywords, and live data, but the demo should highlight these expected concepts:

- CostPilot sees a routine customer support request.
- The payload is short and does not include risk language.
- CostPilot routes the request to the lower-cost Scout tier when policy allows.
- CostPilot records the event so leadership can see governed usage.

Validated live result on July 8, 2026:

- Complexity: Routine.
- Model tier: Scout.
- Routing reason: no complexity keywords detected.
- Cost: about $0.000291 for the request.
- Sensitive terms: none.

## What To Say While Running It

"This is the kind of request that happens all day in support. The customer only needs a short status update."

"Without controls, companies can accidentally send simple work like this to an expensive model over and over again."

"CostPilot evaluates it first, recognizes that it is routine, routes it to the right cost tier, and logs the decision."

## The Wow Moment

Point to the routing/pruning result and say:

"This is the savings story. The customer still gets a useful answer, but the company does not have to pay premium-model pricing for routine work."

## What To Show After The Request

1. Show the model tier selected.
2. Show the routing reason.
3. Show cost for the request.
4. Show that no risk terms were matched.
5. Show the event in the governance stream.
6. Show requests governed and savings on the dashboard.

## Plain-English Explanation For Buyers

"This is where AI cost can quietly grow. A company may have thousands of simple support requests every month. If those requests are routed inefficiently, the AI bill grows without anyone noticing. CostPilot helps make sure simple work stays efficient."

## Plain-English Explanation For Investors Or Advisors

"This scenario shows the recurring cost problem. The value is not one request. The value appears when routine AI work happens thousands of times and CostPilot keeps that usage efficient and measurable."

## Why This Scenario Works

This scenario is strong because it focuses on pure cost control:

- The request is routine.
- The request does not need a premium model.
- The business task is easy to understand.
- The savings story is simple: cheaper routing for routine work.

## Short Version To Say Out Loud

"A routine support request comes in. CostPilot recognizes that it does not need a premium model, routes it to Scout, and still gives leadership a record of the decision."

## Follow-Up Question

After showing the demo, ask:

"How many routine AI requests like this do you think your teams will generate once agents are widely adopted?"
