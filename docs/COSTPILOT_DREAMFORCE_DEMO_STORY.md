# CostPilot Dreamforce Demo Story

## Demo Name

The Renewal Request That Should Not Go Straight To AI

## Demo Goal

Show that CostPilot can make one AI request easier to understand, cheaper to process, safer to govern, and easier to explain to leadership.

The viewer should walk away thinking:

"I can see why companies will need this once AI agents are everywhere."

## The Setup

A customer success or sales team uses an AI agent to help with renewal requests. A customer sends a long email thread asking for help with a renewal quote. The message includes useful business context, but it also includes repeated email headers, old replies, signatures, disclaimers, and legal language.

Without CostPilot, the entire payload could be sent to a premium model with no clear record of why, what it cost, or whether it contained risk.

With CostPilot, the request is evaluated before the AI call.

## Fictional Company

Customer: Meridian Retail Group

Internal team: Sales

AI agent: Renewal Assist Agent

Platform: Salesforce / Agentforce-style workflow

## Demo Payload

Use this as the customer request or demo CRM description.

```text
Subject: RE: RE: RE: Renewal quote review and next steps for Meridian Retail Group

From: Dana Morris <dana.morris@meridianretail.example>
To: renewals@company.example
CC: legal@meridianretail.example; procurement@meridianretail.example
Date: Tuesday, September 8, 2026 at 8:14 AM

Hi team,

We are reviewing the 25-license renewal quote for our customer support and field operations teams. Before we approve the renewal, our leadership team needs a short summary of the business terms, discount rationale, contract risk, and next steps.

The current quote is acceptable in principle, but procurement is asking whether the multi-year discount can be justified based on current usage and expected expansion. Legal also asked us to confirm whether any renewal language creates liability, confidentiality, or service-level concerns before signature.

Please summarize the renewal request in plain English, identify anything that should be reviewed by legal, and draft a short follow-up response to the customer.

-----Original Message-----
From: Dana Morris <dana.morris@meridianretail.example>
Sent: Monday, September 7, 2026 at 4:52 PM
To: renewals@company.example
Subject: Renewal quote review

Following up on the renewal quote. We need to understand whether the discount is still valid if we add five more licenses next quarter. Please also confirm whether the contract terms changed from last year.

-----Original Message-----
From: Renewals Team <renewals@company.example>
Sent: Monday, September 7, 2026 at 3:41 PM
To: Dana Morris <dana.morris@meridianretail.example>
Subject: Renewal quote

Attached is the renewal estimate for 25 licenses. The customer has requested a summary, discount justification, and next steps.

-----Original Message-----
From: System Notification <no-reply@crm.example>
Sent: Monday, September 7, 2026 at 3:40 PM
Subject: CRM case update

Case updated. Renewal opportunity moved to review stage. Customer requested summary. Customer requested pricing clarification. Customer requested legal review. Customer requested renewal next steps.

Confidentiality Notice: This email and any attachments may contain confidential or privileged information intended only for the recipient. If you received this message in error, please delete it. This notice may be repeated in long email chains and does not usually help answer the customer request.

Signature:
Dana Morris
VP Operations
Meridian Retail Group
555-0100

Signature:
Renewals Team
Customer Success Operations
Company Example
```

## What CostPilot Should Show

The exact result may vary depending on current model rules, keywords, and live data, but the demo should highlight these expected concepts:

- CostPilot sees a long, messy business payload.
- The request includes repeated headers, signatures, and old thread content that can be pruned.
- The request includes complexity/risk signals such as renewal, contract, legal, liability, confidentiality, discount, and procurement.
- CostPilot should route or escalate based on the configured policy.
- CostPilot should record the decision in the audit log.
- The dashboard should show the request as governed activity.

## What To Say While Running It

"This looks like a normal renewal request. But it has a few things going on at once: useful business context, repeated email thread noise, legal language, and contract-related risk."

"Without a control layer, this kind of payload can go straight to an expensive model and nobody has a clean answer for what happened."

"CostPilot evaluates the request first. It can prune the repeated junk, detect the risk, route to the right model tier, and log the decision."

## The Wow Moment

Point to the decision detail and say:

"This is the part I want leaders to care about. CostPilot is not just giving an AI answer. It is creating a record of the decision: what came in, what was removed, why it routed the way it did, what it cost, and whether risk was present."

## What To Show After The Request

1. Show the CostPilot rationale.
2. Show pruning details if available.
3. Show the model tier selected.
4. Show risk level and matched keywords.
5. Show the audit log record.
6. Show the dashboard roll-up.

## Plain-English Explanation For Buyers

"This is what happens thousands of times across a company once AI agents are deployed. CostPilot helps leaders understand which requests are simple, which ones are risky, which ones cost more than they should, and where AI activity is happening."

## Plain-English Explanation For Investors Or Advisors

"The product is aimed at the management layer around AI adoption. As AI agents spread, companies need visibility into cost, routing, risk, and accountability. CostPilot is testing whether that layer is valuable enough to become a product category."

## Why This Scenario Works

This scenario is strong because it combines three buyer concerns:

- Cost: the payload contains unnecessary text that can be pruned.
- Risk: the request includes legal and contract language.
- Accountability: the decision can be logged and shown to leadership.

## Short Version To Say Out Loud

"A messy renewal request comes in. CostPilot removes unnecessary context, detects legal and contract risk, routes it to the right model tier, and gives leadership a record of what happened."

## Follow-Up Question

After showing the demo, ask:

"If your company had hundreds or thousands of AI requests like this every month, who would be responsible for explaining the cost and risk?"
